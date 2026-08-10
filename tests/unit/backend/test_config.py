"""Process-setting contracts for live runtime limits."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from fleet_rlm.config import Settings, active_profile_contract, load_profile_environment_contracts


def test_profile_environment_matrix_follows_selected_toml_policy() -> None:
    contracts = {contract.name: contract for contract in load_profile_environment_contracts()}

    assert active_profile_contract().name == "daytona-recursive"
    assert contracts["daytona-recursive"].provider == "OpenCode Go"
    assert contracts["daytona-recursive"].provider_environment_names == (
        "FLEET_DAYTONA_API_KEY",
        "FLEET_OPENCODE_GO_API_KEY",
        "FLEET_OPENCODE_GO_BASE_URL",
    )
    assert contracts["daytona-managed"].provider == "Databricks AI Gateway"
    assert contracts["daytona-managed"].managed_policy_environment_names == (
        "FLEET_DAYTONA_API_KEY",
        "DATABRICKS_TOKEN",
        "FLEET_DATABRICKS_AI_GATEWAY_BASE_URL",
        "FLEET_DATABASE_URL",
        "FLEET_MLFLOW_EXPERIMENT_NAME",
        "FLEET_MLFLOW_TRACE_CATALOG",
        "FLEET_MLFLOW_TRACE_SCHEMA",
        "FLEET_MLFLOW_TRACE_TABLE_PREFIX",
        "FLEET_MLFLOW_TRACING_SQL_WAREHOUSE_ID",
    )


def test_daytona_profile_uses_specialized_bounded_model_roles() -> None:
    policy_path = Path(__file__).resolve().parents[3] / "config" / "fleet.toml"
    document = tomllib.loads(policy_path.read_text(encoding="utf-8"))

    assert set(document["profiles"]) == {
        "daytona",
        "daytona-recursive",
        "daytona-managed",
        "daytona-bench",
        "daytona-bench-40",
    }
    assert document["defaults"]["daytona"]["snapshot"] == "fleet-rlm-python313-v5"
    llm = document["profiles"]["daytona"]["llm"]
    assert llm["root"] == {
        "model": "deepseek-v4-flash",
        "api_key_env": "FLEET_OPENCODE_GO_API_KEY",
        "base_url_env": "FLEET_OPENCODE_GO_BASE_URL",
        "max_tokens": 16000,
        # Cache hits provide no fresh action observation and can read as a frozen stream.
        "cache": False,
        "reasoning_effort": "low",
    }
    assert llm["sub"] == {
        "model": "deepseek-v4-flash",
        "api_key_env": "FLEET_OPENCODE_GO_API_KEY",
        "base_url_env": "FLEET_OPENCODE_GO_BASE_URL",
        "max_tokens": 16000,
        "temperature": 0,
        "cache": False,
        "reasoning_effort": "low",
    }
    assert document["defaults"]["llm"] == {
        "root": {"model_provider_service": "uscentral.default.zencode-oai"},
        "sub": {"model_provider_service": "uscentral.default.zencode-oai"},
    }
    assert document["defaults"]["runtime"]["live_enabled"] is True


@pytest.mark.parametrize(
    "profile",
    ("daytona", "daytona-recursive", "daytona-managed", "daytona-bench", "daytona-bench-40"),
)
def test_all_daytona_profiles_use_deepseek_v4_flash_for_both_roles(profile: str) -> None:
    policy_path = Path(__file__).resolve().parents[3] / "config" / "fleet.toml"
    document = tomllib.loads(policy_path.read_text(encoding="utf-8"))

    llm = document["profiles"][profile]["llm"]
    assert llm["root"]["model"] == "deepseek-v4-flash"
    assert llm["sub"]["model"] == "deepseek-v4-flash"
    if profile in {"daytona", "daytona-recursive"}:
        # The interactive profiles serve the OpenCode Go gateway (the
        # recursive profile must mirror the daytona llm section exactly);
        # every other profile stays on the Databricks AI Gateway.
        expected_key_env, expected_base_env = "FLEET_OPENCODE_GO_API_KEY", "FLEET_OPENCODE_GO_BASE_URL"
    else:
        expected_key_env, expected_base_env = "DATABRICKS_TOKEN", "FLEET_DATABRICKS_AI_GATEWAY_BASE_URL"
    assert llm["root"]["api_key_env"] == expected_key_env
    assert llm["sub"]["api_key_env"] == expected_key_env
    assert llm["root"]["base_url_env"] == expected_base_env
    assert llm["sub"]["base_url_env"] == expected_base_env


def test_daytona_profile_routes_tracing_to_supervised_local_mlflow() -> None:
    policy_path = Path(__file__).resolve().parents[3] / "config" / "fleet.toml"
    document = tomllib.loads(policy_path.read_text(encoding="utf-8"))

    assert document["profiles"]["daytona"]["mlflow"] == {
        "tracing_enabled": True,
        "tracking_uri": "http://127.0.0.1:5001",
        "experiment_name": "fleet-rlm",
    }


def test_default_mlflow_policy_uses_async_full_fidelity_trace_delivery() -> None:
    policy_path = Path(__file__).resolve().parents[3] / "config" / "fleet.toml"
    document = tomllib.loads(policy_path.read_text(encoding="utf-8"))

    assert document["defaults"]["mlflow"] == {
        "tracing_enabled": True,
        "expose_trace_id": True,
        "async_logging": True,
        "trace_sampling_ratio": 1.0,
        "trace_content_max_chars": 10000,
    }


def test_daytona_managed_profile_declares_lakebase_and_mlflow_environment_references() -> None:
    policy_path = Path(__file__).resolve().parents[3] / "config" / "fleet.toml"
    document = tomllib.loads(policy_path.read_text(encoding="utf-8"))

    managed = document["profiles"]["daytona-managed"]
    assert managed["runtime"] == {"environment": "daytona"}
    assert managed["mlflow"] == {
        "tracing_enabled": True,
        "tracking_uri": "databricks",
        "experiment_name_env": "FLEET_MLFLOW_EXPERIMENT_NAME",
        "trace_catalog_env": "FLEET_MLFLOW_TRACE_CATALOG",
        "trace_schema_env": "FLEET_MLFLOW_TRACE_SCHEMA",
        "trace_table_prefix_env": "FLEET_MLFLOW_TRACE_TABLE_PREFIX",
        "tracing_sql_warehouse_id_env": "FLEET_MLFLOW_TRACING_SQL_WAREHOUSE_ID",
    }
    assert document["defaults"]["storage"]["database_url_env"] == "FLEET_DATABASE_URL"


def test_daytona_managed_profile_resolves_lakebase_and_managed_mlflow_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import fleet_rlm.config as config

    _select_profile(tmp_path, profile="daytona-managed", monkeypatch=monkeypatch)
    (tmp_path / ".env").write_text(
        "FLEET_DATABASE_URL=postgresql://dotenv-user:dotenv-password@lakebase.example/fleet_rlm?sslmode=require\n"
        "FLEET_MLFLOW_EXPERIMENT_NAME=dotenv-fleet\n"
        "FLEET_MLFLOW_TRACE_CATALOG=dotenv_catalog\n"
        "FLEET_MLFLOW_TRACE_SCHEMA=dotenv_schema\n"
        "FLEET_MLFLOW_TRACE_TABLE_PREFIX=dotenv_prefix\n"
        "FLEET_MLFLOW_TRACING_SQL_WAREHOUSE_ID=dotenv-warehouse\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    for name in (
        "FLEET_MLFLOW_EXPERIMENT_NAME",
        "FLEET_MLFLOW_TRACE_SCHEMA",
        "FLEET_MLFLOW_TRACE_TABLE_PREFIX",
        "FLEET_MLFLOW_TRACING_SQL_WAREHOUSE_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FLEET_DAYTONA_API_KEY", "process-daytona-key")
    monkeypatch.setenv("DATABRICKS_TOKEN", "process-databricks-token")
    monkeypatch.setenv("FLEET_DATABRICKS_AI_GATEWAY_BASE_URL", "https://gateway.example.test/v1")
    monkeypatch.setenv(
        "FLEET_DATABASE_URL",
        "postgresql://process-user:process-password@lakebase.example/fleet_rlm?sslmode=require",
    )
    monkeypatch.setenv("FLEET_MLFLOW_TRACE_CATALOG", "process_catalog")

    settings = config.load_runtime_settings()

    assert settings.run_environment == "daytona"
    assert settings.database_url == (
        "postgresql://process-user:process-password@lakebase.example/fleet_rlm?sslmode=require"
    )
    assert settings.mlflow_tracking_uri == "databricks"
    assert settings.mlflow_experiment_name == "dotenv-fleet"
    assert settings.mlflow_trace_catalog == "process_catalog"
    assert settings.mlflow_trace_schema == "dotenv_schema"
    assert settings.mlflow_trace_table_prefix == "dotenv_prefix"
    assert settings.mlflow_tracing_sql_warehouse_id == "dotenv-warehouse"


def test_daytona_managed_profile_requires_declared_database_and_mlflow_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import fleet_rlm.config as config

    _select_profile(tmp_path, profile="daytona-managed", monkeypatch=monkeypatch)
    monkeypatch.chdir(tmp_path)
    for name in (
        "FLEET_DATABASE_URL",
        "FLEET_DAYTONA_API_KEY",
        "DATABRICKS_TOKEN",
        "FLEET_DATABRICKS_AI_GATEWAY_BASE_URL",
        "FLEET_MLFLOW_EXPERIMENT_NAME",
        "FLEET_MLFLOW_TRACE_CATALOG",
        "FLEET_MLFLOW_TRACE_SCHEMA",
        "FLEET_MLFLOW_TRACE_TABLE_PREFIX",
        "FLEET_MLFLOW_TRACING_SQL_WAREHOUSE_ID",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(config.FleetConfigurationError, match="required environment value"):
        config.load_runtime_settings()


def test_daytona_profile_resolves_deepseek_root_and_sub_with_gateway_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.config as config

    monkeypatch.setenv("FLEET_DAYTONA_API_KEY", "test-daytona-key")
    monkeypatch.setenv("FLEET_OPENCODE_GO_API_KEY", "test-opencode-go-key")
    monkeypatch.setenv("FLEET_OPENCODE_GO_BASE_URL", "https://gateway.example.test/v1")

    settings = config.load_runtime_settings()

    assert settings.root_model == "deepseek-v4-flash"
    assert settings.sub_model == "deepseek-v4-flash"
    assert settings.root_llm_model_provider_service == "uscentral.default.zencode-oai"
    assert settings.sub_llm_model_provider_service == "uscentral.default.zencode-oai"
    assert settings.root_llm_reasoning_effort == "low"
    assert settings.sub_llm_reasoning_effort == "low"
    assert settings.sub_llm_temperature == 0
    # Live observation turns disable the LM cache: a hit would provide no fresh
    # action observation and read as a frozen stream.
    assert settings.root_llm_cache is False
    assert settings.sub_llm_cache is False
    assert settings.root_llm_max_tokens == settings.sub_llm_max_tokens == 16000
    assert settings.mlflow_tracing_enabled is True
    assert settings.mlflow_tracking_uri == "http://127.0.0.1:5001"


def test_daytona_ignores_managed_mlflow_environment_values_when_not_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.config as config

    monkeypatch.setenv("FLEET_DAYTONA_API_KEY", "test-daytona-key")
    monkeypatch.setenv("FLEET_OPENCODE_GO_API_KEY", "test-opencode-go-key")
    monkeypatch.setenv("FLEET_OPENCODE_GO_BASE_URL", "https://gateway.example.test/v1")
    monkeypatch.setenv("FLEET_MLFLOW_EXPERIMENT_NAME", "managed-experiment")
    monkeypatch.setenv("FLEET_MLFLOW_TRACE_CATALOG", "managed_catalog")

    settings = config.load_runtime_settings()

    assert settings.mlflow_tracking_uri == "http://127.0.0.1:5001"
    assert settings.mlflow_experiment_name == "fleet-rlm"
    assert settings.mlflow_trace_catalog is None


def test_recursive_depth_is_a_recursive_execution_invariant_not_a_setting() -> None:
    from fleet_rlm.composition.common import recursive_rlm_options
    from fleet_rlm.rlm.recursive_calls import RLM_NATIVE_CHILD_DEPTH

    settings = Settings(_env_file=None)
    assert not hasattr(settings, "rlm_recursion_max_depth")
    options = recursive_rlm_options(settings)
    assert RLM_NATIVE_CHILD_DEPTH == 1
    assert not hasattr(options, "max_depth")


def test_stale_recursive_depth_policy_key_fails_validation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import fleet_rlm.config as config

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

    with pytest.raises(config.FleetConfigurationError, match="recursion_max_depth"):
        config.load_runtime_settings()


def test_recursive_daytona_profile_enables_only_the_recursive_tool_policy() -> None:
    policy_path = Path(__file__).resolve().parents[3] / "config" / "fleet.toml"
    document = tomllib.loads(policy_path.read_text(encoding="utf-8"))

    assert document["defaults"]["rlm"]["recursion_enabled"] is False
    assert document["profiles"]["daytona-recursive"]["rlm"] == {"recursion_enabled": True}
    assert document["profiles"]["daytona-recursive"]["llm"] == document["profiles"]["daytona"]["llm"]


@pytest.mark.parametrize("value", ("", "   "))
def test_model_provider_service_rejects_blank_values(value: str) -> None:
    with pytest.raises(ValidationError, match="model_provider_service"):
        Settings(_env_file=None, root_llm_model_provider_service=value)


def test_daytona_benchmark_profiles_use_compatible_models_without_cache_or_mlflow() -> None:
    policy_path = Path(__file__).resolve().parents[3] / "config" / "fleet.toml"
    document = tomllib.loads(policy_path.read_text(encoding="utf-8"))

    for profile in ("daytona-bench", "daytona-bench-40"):
        policy = document["profiles"][profile]
        assert policy["runtime"]["environment"] == "daytona"
        # Bench profiles stay traceless by explicitly declaring mlflow disabled,
        # overriding the on-by-default [defaults.mlflow] policy.
        assert policy["mlflow"]["tracing_enabled"] is False
        for role in ("root", "sub"):
            llm = policy["llm"][role]
            assert llm["model"] == "deepseek-v4-flash"
            assert llm["api_key_env"] == "DATABRICKS_TOKEN"
            assert llm["base_url_env"] == "FLEET_DATABRICKS_AI_GATEWAY_BASE_URL"
            assert llm["cache"] is False
            assert llm["max_tokens"] == 8000
            assert "reasoning_effort" not in llm

    assert document["profiles"]["daytona-bench"]["rlm"] == {"verbose": False}
    assert document["profiles"]["daytona-bench-40"]["rlm"] == {"max_iterations": 40}


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
    monkeypatch.setattr("fleet_rlm.config._CONFIG_PATH", policy)
    return policy


@pytest.mark.parametrize(("profile", "iterations"), [("daytona-bench", 20), ("daytona-bench-40", 40)])
def test_daytona_benchmark_profiles_resolve_without_mlflow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    profile: str,
    iterations: int,
) -> None:
    import fleet_rlm.config as config

    _select_profile(tmp_path, profile=profile, monkeypatch=monkeypatch)
    monkeypatch.setenv("FLEET_DAYTONA_API_KEY", "test-daytona-key")
    # Benchmark profiles keep the Databricks AI Gateway; only the interactive
    # daytona/daytona-recursive profiles use the OpenCode Go gateway.
    monkeypatch.setenv("DATABRICKS_TOKEN", "test-databricks-token")
    monkeypatch.setenv("FLEET_DATABRICKS_AI_GATEWAY_BASE_URL", "https://gateway.example.test/v1")

    settings = config.load_runtime_settings()

    assert settings.run_environment == "daytona"
    assert settings.root_model == "deepseek-v4-flash"
    assert settings.sub_model == settings.root_model
    assert settings.root_llm_model_provider_service == "uscentral.default.zencode-oai"
    assert settings.sub_llm_model_provider_service == "uscentral.default.zencode-oai"
    assert settings.daytona_snapshot == "fleet-rlm-python313-v5"
    assert settings.root_llm_cache is False
    assert settings.sub_llm_cache is False
    assert settings.rlm_max_iterations == iterations
    assert settings.mlflow_tracing_enabled is False


def test_removed_databricks_daytona_profile_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import fleet_rlm.config as config

    # unknown default_profile in the committed policy is rejected even when it
    # names a previously existing profile
    _select_profile(tmp_path, profile="databricks-daytona", monkeypatch=monkeypatch)

    with pytest.raises(config.FleetConfigurationError, match="configured profile does not exist"):
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
max_iterations = 3
max_llm_calls = 4
max_output_chars = 500
max_execution_output_chars = 250
execution_timeout_s = 90
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
    import fleet_rlm.config as config

    policy = tmp_path / "fleet.toml"
    _policy(policy)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "_CONFIG_PATH", policy)
    monkeypatch.delenv("FLEET_RUN_ENVIRONMENT", raising=False)

    settings = config.load_runtime_settings()

    assert settings.run_environment == "daytona"
    assert settings.live_enabled is True
    assert settings.rlm_max_iterations == 3
    assert settings.max_url_bytes == 30
    assert settings.root_lm.model == "openai/root"
    assert settings.sub_lm.temperature == 0.2
    assert settings.lm_roles.root.model_provider_service is None
    assert settings.lm_roles.sub.api_key_env == "SUB_KEY"


def test_omitted_role_cache_and_retry_defaults_resolve_to_settings_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import fleet_rlm.config as config

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
    import fleet_rlm.config as config

    policy = tmp_path / "fleet.toml"
    _policy(policy)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "_CONFIG_PATH", policy)

    assert config.require_live_execution().live_enabled is True

    policy.write_text(
        policy.read_text(encoding="utf-8").replace("live_enabled = true", "live_enabled = false"), encoding="utf-8"
    )
    with pytest.raises(config.FleetConfigurationError, match=r"runtime\.live_enabled=false"):
        config.require_live_execution()


def test_runtime_settings_ignores_stale_environment_policy_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import fleet_rlm.config as config

    policy = tmp_path / "fleet.toml"
    _policy(policy)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "_CONFIG_PATH", policy)
    monkeypatch.delenv("FLEET_RUN_ENVIRONMENT", raising=False)
    monkeypatch.setenv("FLEET_ROOT_MODEL", "openai/override")
    monkeypatch.setenv("FLEET_RLM_MAX_ITERATIONS", "99")

    settings = config.load_runtime_settings()

    assert settings.root_model == "openai/root"
    assert settings.rlm_max_iterations == 3


def test_runtime_settings_resolves_only_toml_declared_environment_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import fleet_rlm.config as config
    from fleet_rlm.rlm.lm_factory import resolve_role_api_key

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
    import fleet_rlm.config as config
    from fleet_rlm.rlm.lm_factory import resolve_role_api_key

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
    import fleet_rlm.config as config

    policy = tmp_path / "fleet.toml"
    _policy(policy)
    monkeypatch.chdir(tmp_path)
    policy.write_text(
        policy.read_text(encoding="utf-8").replace("verbose = true", "verbose = true\nunexpected = 1"),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "_CONFIG_PATH", policy)

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
    with pytest.raises(ValueError, match=r"retired Fleet environment variable.*FLEET_LIVE_KERNEL"):
        create_app(settings=Settings(_env_file=None))


def test_startup_rejects_retired_budget_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.app import create_app

    monkeypatch.setenv("FLEET_BUDGET_MAX_ITERATIONS", "6")
    with pytest.raises(ValueError, match=r"retired Fleet environment variable.*FLEET_BUDGET_MAX_ITERATIONS"):
        create_app(settings=Settings(_env_file=None))


def test_turn_timeout_defaults_to_thirty_minutes() -> None:
    assert Settings(_env_file=None).turn_timeout_seconds == 1800


def test_live_execution_is_enabled_by_default_and_can_be_disabled() -> None:
    assert Settings(_env_file=None).live_enabled is True
    assert Settings(_env_file=None, live_enabled=False).live_enabled is False


def test_url_source_limit_defaults_to_ten_mebibytes() -> None:
    assert Settings(_env_file=None).max_url_bytes == 10 * 1024 * 1024


def test_mlflow_tracing_field_defaults_to_disabled() -> None:
    # The Settings field default is off; the committed [defaults.mlflow] policy
    # enables it. Test the field default here, policy default in test_config.py.
    assert Settings(_env_file=None).mlflow_tracing_enabled is False
    assert Settings(_env_file=None).mlflow_trace_content_max_chars == 10_000


def test_mlflow_tracing_can_be_disabled_explicitly() -> None:
    assert Settings(_env_file=None, mlflow_tracing_enabled=False).mlflow_tracing_enabled is False


def test_daytona_admission_defaults_to_eight_leases() -> None:
    assert Settings(_env_file=None).max_active_daytona_leases == 8


def test_settings_does_not_read_fleet_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_MAX_ACTIVE_DAYTONA_LEASES", "3")
    assert Settings(_env_file=None).max_active_daytona_leases == 8


def test_settings_ignore_mlflow_environment_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_MLFLOW_TRACE_CATALOG", "analytics")
    monkeypatch.setenv("FLEET_MLFLOW_TRACE_SCHEMA", "traces")
    monkeypatch.setenv("FLEET_MLFLOW_TRACE_TABLE_PREFIX", "fleet_app")
    monkeypatch.setenv("FLEET_MLFLOW_TRACING_SQL_WAREHOUSE_ID", "warehouse-123")

    settings = Settings(_env_file=None)

    assert settings.mlflow_trace_catalog is None
    assert settings.mlflow_trace_schema is None
    assert settings.mlflow_trace_table_prefix is None
    assert settings.mlflow_tracing_sql_warehouse_id is None


@pytest.mark.parametrize("value", [0, -1, 9])
def test_daytona_admission_must_be_between_one_and_eight(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_active_daytona_leases=value)


def test_settings_does_not_read_runtime_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_TURN_TIMEOUT_SECONDS", "1200")
    assert Settings(_env_file=None).turn_timeout_seconds == 1800


@pytest.mark.parametrize("value", [0, -1])
def test_turn_timeout_must_be_positive(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, turn_timeout_seconds=value)
