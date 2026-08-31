"""Authoritative configuration-schema consistency contracts (P29).

These tests pin the one-source-of-truth rule: supported field identity, TOML
location, operator/editor affordances, and defaults derive from the
``FleetFieldPolicy`` declarations on ``Settings`` fields. Hand-editing the
frozen inventory below is only legitimate together with an intentional,
reviewed policy-surface change.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

import fleet_rlm.config.loader as config_loader
import fleet_rlm.config.policy as config_policy
import fleet_rlm.config.settings as config

_EXPECTED_INVENTORY: tuple[tuple[str, str, str, str, tuple[str, ...], str | None], ...] = (
    ("application.name", "Application", "Name", "text", (), "app_name"),
    ("runtime.environment", "Runtime", "Environment", "single_choice", ("daytona",), "run_environment"),
    ("runtime.live_enabled", "Runtime", "Live execution", "boolean", (), "live_enabled"),
    ("runtime.turn_timeout_seconds", "Runtime", "Turn timeout seconds", "number", (), "turn_timeout_seconds"),
    (
        "runtime.max_active_daytona_leases",
        "Runtime",
        "Maximum Daytona leases",
        "number",
        (),
        "max_active_daytona_leases",
    ),
    ("runtime.heartbeat_seconds", "Runtime", "Heartbeat seconds", "number", (), "run_heartbeat_seconds"),
    ("runtime.stale_after_seconds", "Runtime", "Stale after seconds", "number", (), "run_stale_after_seconds"),
    ("llm.root.model", "Root LLM", "Model id", "text", (), "root_model"),
    ("llm.root.api_key_env", "Root LLM", "Provider API key environment variable", "text", (), "root_llm_api_key_env"),
    ("llm.root.base_url", "Root LLM", "Provider base URL", "text", (), "root_llm_base_url"),
    ("llm.root.base_url_env", "Root LLM", "Provider base URL environment variable", "text", (), None),
    ("llm.root.max_tokens", "Root LLM", "Maximum tokens", "number", (), "root_llm_max_tokens"),
    ("llm.root.temperature", "Root LLM", "Temperature", "number", (), "root_llm_temperature"),
    ("llm.root.cache", "Root LLM", "Cache", "boolean", (), "root_llm_cache"),
    ("llm.root.num_retries", "Root LLM", "Retries", "number", (), "root_llm_num_retries"),
    (
        "llm.root.reasoning_effort",
        "Root LLM",
        "Reasoning effort",
        "single_choice",
        ("none", "low", "medium", "high"),
        "root_llm_reasoning_effort",
    ),
    ("llm.sub.model", "Sub LLM", "Model id", "text", (), "sub_model"),
    ("llm.sub.api_key_env", "Sub LLM", "Provider API key environment variable", "text", (), "sub_llm_api_key_env"),
    ("llm.sub.base_url", "Sub LLM", "Provider base URL", "text", (), "sub_llm_base_url"),
    ("llm.sub.base_url_env", "Sub LLM", "Provider base URL environment variable", "text", (), None),
    ("llm.sub.max_tokens", "Sub LLM", "Maximum tokens", "number", (), "sub_llm_max_tokens"),
    ("llm.sub.temperature", "Sub LLM", "Temperature", "number", (), "sub_llm_temperature"),
    ("llm.sub.cache", "Sub LLM", "Cache", "boolean", (), "sub_llm_cache"),
    ("llm.sub.num_retries", "Sub LLM", "Retries", "number", (), "sub_llm_num_retries"),
    (
        "llm.sub.reasoning_effort",
        "Sub LLM",
        "Reasoning effort",
        "single_choice",
        ("none", "low", "medium", "high"),
        "sub_llm_reasoning_effort",
    ),
    ("rlm.max_iters", "RLM", "Maximum iterations", "number", (), "rlm_max_iters"),
    ("rlm.max_llm_calls", "RLM", "Maximum LLM calls", "number", (), "rlm_max_llm_calls"),
    ("rlm.max_output_chars", "RLM", "Maximum output characters", "number", (), "rlm_max_output_chars"),
    (
        "rlm.max_execution_output_chars",
        "RLM",
        "Maximum execution output characters",
        "number",
        (),
        "rlm_max_execution_output_chars",
    ),
    ("rlm.execution_timeout_s", "RLM", "Sandbox execution timeout (seconds)", "number", (), "rlm_execution_timeout_s"),
    ("rlm.recursion_enabled", "RLM", "Enable recursive child RLMs", "boolean", (), "rlm_recursion_enabled"),
    ("rlm.recursion_max_calls", "RLM", "Recursive maximum calls", "number", (), "rlm_recursion_max_calls"),
    (
        "rlm.recursion_max_prompt_chars",
        "RLM",
        "Recursive prompt character bound",
        "number",
        (),
        "rlm_recursion_max_prompt_chars",
    ),
    ("rlm.recursion_child_max_iters", "RLM", "Child maximum iterations", "number", (), "rlm_recursion_child_max_iters"),
    (
        "rlm.recursion_child_max_llm_calls",
        "RLM",
        "Child maximum LLM calls",
        "number",
        (),
        "rlm_recursion_child_max_llm_calls",
    ),
    (
        "rlm.recursion_child_max_output_chars",
        "RLM",
        "Child maximum output characters",
        "number",
        (),
        "rlm_recursion_child_max_output_chars",
    ),
    (
        "rlm.recursion_max_parallel_children",
        "RLM",
        "Maximum parallel child RLMs",
        "number",
        (),
        "rlm_recursion_max_parallel_children",
    ),
    (
        "rlm.autonomous_memory_categories",
        "RLM",
        "Autonomous Memory categories",
        "string_list",
        (),
        "rlm_autonomous_memory_categories",
    ),
    ("rlm.verbose", "RLM", "DSPy host verbose logging", "boolean", (), "rlm_verbose"),
    ("storage.data_root", "Storage", "Data root", "text", (), "data_root"),
    ("storage.max_upload_bytes", "Storage", "Maximum upload bytes", "number", (), "max_upload_bytes"),
    ("storage.max_url_bytes", "Storage", "Maximum URL source bytes", "number", (), "max_url_bytes"),
    ("storage.max_artifact_bytes", "Storage", "Maximum artifact bytes", "number", (), "max_artifact_bytes"),
    ("storage.database_url_env", "Storage", "Database URL environment variable", "text", (), None),
    ("daytona.api_key_env", "Daytona", "API key environment variable", "text", (), None),
    ("daytona.snapshot", "Daytona", "Snapshot", "text", (), "daytona_snapshot"),
    ("daytona.org_id", "Daytona", "Organization ID", "text", (), "daytona_org_id"),
    ("daytona.volume_name", "Daytona", "Volume name", "text", (), "volume_name"),
    ("daytona.volume_mount_path", "Daytona", "Volume mount path", "text", (), "volume_mount_path"),
    (
        "logging.level",
        "Logging",
        "Level",
        "single_choice",
        ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"),
        "log_level",
    ),
    ("mlflow.tracing_enabled", "MLflow", "Tracing enabled", "boolean", (), "mlflow_tracing_enabled"),
    ("mlflow.async_logging", "MLflow", "Async trace logging", "boolean", (), "mlflow_async_logging"),
    ("mlflow.trace_sampling_ratio", "MLflow", "Trace sampling ratio", "number", (), "mlflow_trace_sampling_ratio"),
    (
        "mlflow.trace_content_max_chars",
        "MLflow",
        "Trace payload character limit",
        "number",
        (),
        "mlflow_trace_content_max_chars",
    ),
    ("mlflow.experiment_name", "MLflow", "Experiment name", "text", (), "mlflow_experiment_name"),
    ("mlflow.experiment_name_env", "MLflow", "Experiment environment variable", "text", (), None),
    ("mlflow.tracking_uri", "MLflow", "Tracking URI", "text", (), "mlflow_tracking_uri"),
    ("mlflow.expose_trace_id", "MLflow", "Expose trace ID", "boolean", (), "mlflow_expose_trace_id"),
    ("mlflow.trace_catalog", "MLflow", "Trace catalog", "text", (), "mlflow_trace_catalog"),
    ("mlflow.trace_catalog_env", "MLflow", "Trace catalog environment variable", "text", (), None),
    ("mlflow.trace_schema", "MLflow", "Trace schema", "text", (), "mlflow_trace_schema"),
    ("mlflow.trace_schema_env", "MLflow", "Trace schema environment variable", "text", (), None),
    ("mlflow.trace_table_prefix", "MLflow", "Trace table prefix", "text", (), "mlflow_trace_table_prefix"),
    ("mlflow.trace_table_prefix_env", "MLflow", "Trace table prefix environment variable", "text", (), None),
    (
        "mlflow.tracing_sql_warehouse_id",
        "MLflow",
        "Tracing SQL warehouse ID",
        "text",
        (),
        "mlflow_tracing_sql_warehouse_id",
    ),
    ("mlflow.tracing_sql_warehouse_id_env", "MLflow", "Tracing SQL warehouse environment variable", "text", (), None),
    ("posthog.enabled", "PostHog", "Analytics enabled", "boolean", (), "posthog_enabled"),
    ("posthog.project_token_env", "PostHog", "Project token environment variable", "text", (), None),
    ("posthog.host", "PostHog", "Ingestion host", "text", (), "posthog_host"),
    ("rlm.wrap_up_seconds", "RLM", "Final-answer reserve (seconds)", "number", (), "rlm_wrap_up_seconds"),
    ("llm.root.timeout_seconds", "Root LLM", "Provider timeout seconds", "number", (), "root_llm_timeout_seconds"),
    ("llm.sub.timeout_seconds", "Sub LLM", "Provider timeout seconds", "number", (), "sub_llm_timeout_seconds"),
)


def test_every_settings_field_carries_one_authoritative_declaration() -> None:
    """Removing/duplicating a policy declaration fails the schema build loudly."""
    policies = config._field_policies()
    assert set(policies) == set(config.Settings.model_fields)


def test_policy_inventory_is_derived_from_the_schema_identically() -> None:
    """The editor inventory built by ``config_policy`` is the schema inventory."""
    derived = tuple(
        (spec.toml_path, spec.group, spec.label, spec.editor, spec.choices, spec.settings_field)
        for spec in config.config_field_specs()
        if spec.group is not None
    )
    current = tuple(
        (field.path, field.group, field.label, field.editor, field.choices, field.settings_field)
        for field in config_policy._FIELDS
    )
    assert current == derived


def test_policy_inventory_matches_the_frozen_operator_surface() -> None:
    current = tuple(
        (field.path, field.group, field.label, field.editor, field.choices, field.settings_field)
        for field in config_policy._FIELDS
    )
    assert current == _EXPECTED_INVENTORY


def test_derived_table_keys_cover_exactly_the_supported_toml_surface() -> None:
    document = config_loader._read_policy_document(Path("config/fleet.toml"))
    for scope_name, scope in [("defaults", document.defaults)] + [
        (name, profile) for name, profile in document.profiles.items()
    ]:
        for section_name, section in scope.items():
            assert section_name in config_loader._TABLE_KEYS, f"{scope_name}.{section_name}"
            for key, value in section.items():
                assert key in config_loader._TABLE_KEYS[section_name], f"{scope_name}.{section_name}.{key}"
                if section_name == "llm":
                    assert isinstance(value, dict)
                    for role_key in value:
                        assert role_key in config_loader._ROLE_KEYS, f"{scope_name}.llm.{key}.{role_key}"


def test_environment_reference_specs_reference_real_fields_and_follow_naming() -> None:
    policies = config._field_policies()
    for spec in config._ENVIRONMENT_REFERENCE_SPECS:
        assert spec.toml_path.endswith("_env")
        assert spec.resolves_to in policies


def test_schema_build_rejects_reference_to_nonexistent_field(monkeypatch: pytest.MonkeyPatch) -> None:
    bogus = config.EnvironmentReferenceSpec(
        toml_path="storage.bogus_env",
        group="Storage",
        label="Bogus environment variable",
        rank=999,
        resolves_to="not_a_settings_field",
    )
    monkeypatch.setattr(config, "_ENVIRONMENT_REFERENCE_SPECS", (*config._ENVIRONMENT_REFERENCE_SPECS, bogus))

    with pytest.raises(config.FleetConfigurationError, match="not_a_settings_field"):
        config._build_field_specs()


def test_schema_build_rejects_duplicate_ranks(monkeypatch: pytest.MonkeyPatch) -> None:
    colliding = config.EnvironmentReferenceSpec(
        toml_path="storage.colliding_env",
        group="Storage",
        label="Colliding environment variable",
        rank=0,
        resolves_to="database_url",
    )
    monkeypatch.setattr(config, "_ENVIRONMENT_REFERENCE_SPECS", (*config._ENVIRONMENT_REFERENCE_SPECS, colliding))

    with pytest.raises(config.FleetConfigurationError, match="duplicate policy inventory rank"):
        config._build_field_specs()


def test_secret_fields_are_never_operator_editable() -> None:
    editable = {spec.settings_field for spec in config.config_field_specs() if spec.group is not None}
    policies = config._field_policies()
    secret_fields = {name for name, meta in policies.items() if meta.secret}
    assert secret_fields == {"daytona_api_key", "llm_api_key", "posthog_project_token"}
    assert editable.isdisjoint(secret_fields)


def test_unknown_direct_settings_field_is_rejected_without_leaking_values() -> None:
    with pytest.raises(config.FleetConfigurationError) as excinfo:
        config.Settings(data_rooot="super-secret-payload")  # type: ignore[call-arg]

    message = str(excinfo.value)
    assert "data_rooot" in message
    assert "super-secret-payload" not in message


def test_retired_settings_kwargs_are_rejected() -> None:
    with pytest.raises(config.FleetConfigurationError, match="_env_file"):
        config.Settings(_env_file=None)  # type: ignore[call-arg]


def test_model_validate_rejects_unknown_keys() -> None:
    with pytest.raises(config.FleetConfigurationError, match="app_name_extra"):
        config.Settings.model_validate({"app_name_extra": "x"})


def test_known_field_type_errors_remain_pydantic_validation_errors() -> None:
    with pytest.raises(ValidationError, match="turn_timeout_seconds"):
        config.Settings(turn_timeout_seconds=0)


def test_committed_policy_loads_every_profile_identically(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """All supported committed TOML profiles resolve with identical values as documented."""
    import tomllib

    for name in (
        "FLEET_DAYTONA_API_KEY",
        "DATABRICKS_TOKEN",
        "FLEET_DATABRICKS_AI_GATEWAY_BASE_URL",
        "FLEET_MODAL_API_KEY",
        "FLEET_MODAL_BASE_URL",
        "FLEET_DATABASE_URL",
        "POSTHOG_PROJECT_TOKEN",
    ):
        monkeypatch.setenv(name, f"test-{name}")
    for name in (
        "FLEET_MLFLOW_EXPERIMENT_NAME",
        "FLEET_MLFLOW_TRACE_CATALOG",
        "FLEET_MLFLOW_TRACE_SCHEMA",
        "FLEET_MLFLOW_TRACE_TABLE_PREFIX",
        "FLEET_MLFLOW_TRACING_SQL_WAREHOUSE_ID",
    ):
        monkeypatch.setenv(name, f"test-{name}")

    source = Path("config/fleet.toml").read_text(encoding="utf-8")
    profiles = tomllib.loads(source)["profiles"]
    policy = tmp_path / "fleet.toml"
    for profile in profiles:
        updated = re.sub(
            r'^default_profile = "[^"]*"$',
            f'default_profile = "{profile}"',
            source,
            count=1,
            flags=re.MULTILINE,
        )
        policy.write_text(updated, encoding="utf-8")
        monkeypatch.setattr(config_loader, "_CONFIG_PATH", policy)
        settings = config_loader.load_runtime_settings()
        assert settings.run_environment == "daytona"
        assert settings.root_model and settings.sub_model


def test_required_policy_key_reports_its_settings_field() -> None:
    import tomllib

    document = tomllib.loads(Path("config/fleet.toml").read_text(encoding="utf-8"))
    document["defaults"]["runtime"].pop("turn_timeout_seconds")
    profile = document["profiles"][document["config"]["default_profile"]]

    with pytest.raises(config.FleetConfigurationError, match="turn_timeout_seconds"):
        config_loader._flatten_policy(config_loader._deep_merge(document["defaults"], profile))


def test_absent_optional_policy_keys_fall_back_to_settings_defaults() -> None:
    import tomllib

    document = tomllib.loads(Path("config/fleet.toml").read_text(encoding="utf-8"))
    defaults = document["defaults"]
    defaults["rlm"].pop("recursion_max_calls")

    flat = config_loader._flatten_policy(config_loader._deep_merge(defaults, document["profiles"]["daytona"]))

    assert "rlm_recursion_max_calls" not in flat.settings
    # ``Settings`` owns the fallback default; TOML absence stays absent.
    assert config.Settings(**flat.settings).rlm_recursion_max_calls == 4
