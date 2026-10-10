"""Runtime policy loading and environment resolution for Fleet settings.

Reads the required TOML policy, resolves environment-backed secrets through
the configuration, and produces the authoritative ``Settings`` instance.
"""

from __future__ import annotations

import logging
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from dotenv import dotenv_values
from pydantic import SecretStr

from fleet_rlm.config.settings import (
    _ENVIRONMENT_NAME,
    _FIELD_SPECS,
    ConfigFieldSpec,
    FleetConfigurationError,
    Settings,
    _field_policies,
)

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "fleet.toml"
_RETIRED_ENVIRONMENT_VARIABLES = frozenset(
    {
        "FLEET_LIVE_KERNEL",
        "FLEET_UPLOAD_ROOT",
        "FLEET_ARTIFACT_ROOT",
        "FLEET_MAX_TURN_WALL_SECONDS",
        "FLEET_BUDGET_MAX_ITERATIONS",
        "FLEET_BUDGET_MAX_LLM_CALLS",
        "FLEET_BUDGET_MAX_OUTPUT_CHARS",
        "FLEET_BUDGET_MAX_WALL_SECONDS",
        "FLEET_BUDGET_MAX_SUB_LM_CONCURRENCY",
        "FLEET_BUDGET_MAX_TOOL_CALLS",
        "FLEET_BUDGET_MAX_SKILL_LOADS",
    }
)


def reject_retired_environment_variables() -> None:
    """Reject retired process and ``.env`` keys before settings resolution."""
    configured = set(_RETIRED_ENVIRONMENT_VARIABLES.intersection(os.environ))
    for name in dotenv_values(".env"):
        if name in _RETIRED_ENVIRONMENT_VARIABLES:
            configured.add(name)
    if configured:
        names = ", ".join(sorted(configured))
        raise ValueError(f"retired Fleet environment variable(s): {names}")


@dataclass(frozen=True, slots=True)
class ConfigurationEnvironmentContract:
    """Non-secret provider facts derived from ``config/fleet.toml``."""

    runtime_environment: str
    provider: str
    root_model: str
    sub_model: str
    root_api_key_env: str
    sub_api_key_env: str
    root_base_url_env: str | None
    sub_base_url_env: str | None
    root_max_tokens: int | None
    sub_max_tokens: int | None
    daytona_api_key_env: str
    daytona_snapshot_env: str | None
    daytona_child_snapshot_env: str | None
    daytona_org_id_env: str
    database_url_env: str | None
    mlflow_tracing_enabled: bool
    mlflow_tracking_uri: str | None
    mlflow_environment_names: tuple[str, ...]
    recursion_enabled: bool

    @property
    def provider_environment_names(self) -> tuple[str, ...]:
        """Return environment names needed for provider-backed execution."""
        return _unique_environment_names(
            self.daytona_api_key_env,
            self.daytona_org_id_env,
            self.root_api_key_env,
            self.sub_api_key_env,
            self.root_base_url_env,
            self.sub_base_url_env,
        )

    @property
    def daytona_snapshot_environment_names(self) -> tuple[str, ...]:
        """Return non-secret Daytona snapshot variable names for operator tooling."""
        return _unique_environment_names(self.daytona_snapshot_env, self.daytona_child_snapshot_env)


@dataclass(frozen=True, slots=True)
class FlattenedPolicy:
    """TOML-derived ``Settings`` constructor input, split by resolution seam.

    ``settings`` carries TOML-bound values keyed by Settings field name.
    ``environment_references`` maps Settings field names to the environment
    variables named by ``*_env`` TOML keys; only the runtime load seam
    resolves those variables into values.
    """

    settings: Mapping[str, Any]
    environment_references: Mapping[str, str]


_MISSING: Any = object()


def _lookup_toml(mapping: Mapping[str, Any], toml_path: str) -> Any:
    """Return the value at ``toml_path`` or the ``_MISSING`` sentinel."""

    current: Any = mapping
    for part in toml_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _derive_table_keys(specs: tuple[ConfigFieldSpec, ...]) -> tuple[dict[str, frozenset[str]], frozenset[str]]:
    """Derive the supported TOML key surfaces from the authoritative inventory."""

    tables: dict[str, set[str]] = {}
    role_keys: set[str] = set()
    for spec in specs:
        if spec.section == "llm":
            tables.setdefault("llm", set()).add(spec.toml_path.split(".")[1])
            role_keys.add(spec.key)
        else:
            tables.setdefault(spec.section, set()).add(spec.key)
    return {name: frozenset(keys) for name, keys in tables.items()}, frozenset(role_keys)


_TABLE_KEYS, _ROLE_KEYS = _derive_table_keys(_FIELD_SPECS)


_ENV_REFERENCE_KEYS: dict[str, tuple[str, ...]] = {
    section: tuple(
        spec.key for spec in _FIELD_SPECS if spec.section == section and spec.environment_reference_for is not None
    )
    for section in dict.fromkeys(spec.section for spec in _FIELD_SPECS if spec.section != "llm")
}


_EXCLUSIVE_ENV_PAIRS: dict[str, tuple[tuple[str, str], ...]] = {
    section: tuple(
        (direct.key, f"{direct.key}_env")
        for direct in _FIELD_SPECS
        if direct.section == section
        and direct.environment_reference_for is None
        and any(
            env.section == section
            and env.environment_reference_for == direct.settings_field
            and env.key == f"{direct.key}_env"
            for env in _FIELD_SPECS
        )
    )
    for section in dict.fromkeys(spec.section for spec in _FIELD_SPECS if spec.section != "llm")
}


_SECRET_RESOLVED_FIELDS: frozenset[str] = frozenset(
    spec.environment_reference_for
    for spec in _FIELD_SPECS
    if spec.environment_reference_for is not None and _field_policies()[spec.environment_reference_for].secret
)


def _require_mapping(value: object, location: str) -> Mapping[str, Any]:
    """
    Require a TOML value to be a mapping.

    Parameters:
        value (object): Value to validate.
        location (str): Configuration path used in the validation error.

    Returns:
        Mapping[str, Any]: The validated mapping.

    Raises:
        FleetConfigurationError: If the value is not a mapping.
    """
    if not isinstance(value, Mapping):
        raise FleetConfigurationError(f"{location} must be a TOML table")
    return cast(Mapping[str, Any], value)


def _validate_policy_table(value: object, location: str) -> None:
    """
    Validate the structure and environment references in a runtime policy table.

    Parameters:
        value (object): Policy table to validate.
        location (str): Configuration path used in validation errors.

    Raises:
        FleetConfigurationError: If the table contains unknown keys, conflicting values,
            or invalid environment references.
    """
    table = _require_mapping(value, location)
    unknown = set(table).difference(_TABLE_KEYS)
    if unknown:
        raise FleetConfigurationError(f"unknown configuration key(s) at {location}: {', '.join(sorted(unknown))}")
    for name, child in table.items():
        if name != "llm":
            allowed = _TABLE_KEYS[name]
            child_table = _require_mapping(child, f"{location}.{name}")
            extras = set(child_table).difference(allowed)
            if extras:
                raise FleetConfigurationError(
                    f"unknown configuration key(s) at {location}.{name}: {', '.join(sorted(extras))}"
                )
            continue
        llm = _require_mapping(child, f"{location}.llm")
        unknown_roles = set(llm).difference(_TABLE_KEYS["llm"])
        if unknown_roles:
            raise FleetConfigurationError(f"unknown LLM role(s) at {location}.llm: {', '.join(sorted(unknown_roles))}")
        for role, role_value in llm.items():
            role_table = _require_mapping(role_value, f"{location}.llm.{role}")
            role_extras = set(role_table).difference(_ROLE_KEYS)
            if role_extras:
                raise FleetConfigurationError(
                    f"unknown configuration key(s) at {location}.llm.{role}: {', '.join(sorted(role_extras))}"
                )
            if "base_url" in role_table and "base_url_env" in role_table:
                raise FleetConfigurationError(f"{location}.llm.{role} cannot define both base_url and base_url_env")
            _validate_environment_reference(role_table.get("api_key_env"), f"{location}.llm.{role}.api_key_env")
            _validate_optional_environment_reference(
                role_table.get("base_url_env"), f"{location}.llm.{role}.base_url_env"
            )
        continue
    for name, child in table.items():
        if name == "llm":
            continue
        child_table = _require_mapping(child, f"{location}.{name}")
        for direct_key, env_key in _EXCLUSIVE_ENV_PAIRS.get(name, ()):
            if direct_key in child_table and env_key in child_table:
                raise FleetConfigurationError(f"{location}.{name} cannot define both {direct_key} and {env_key}")
        for env_key in _ENV_REFERENCE_KEYS.get(name, ()):
            _validate_optional_environment_reference(child_table.get(env_key), f"{location}.{name}.{env_key}")


def _validate_environment_reference(value: object, location: str) -> str:
    """
    Validate and return an uppercase environment-variable name.

    Parameters:
        value (object): Value to validate as an environment-variable name
        location (str): Configuration location used in validation errors

    Returns:
        str: The validated environment-variable name

    Raises:
        FleetConfigurationError: If the value is not a valid uppercase environment-variable name
    """
    if not isinstance(value, str) or not _ENVIRONMENT_NAME.fullmatch(value):
        raise FleetConfigurationError(f"{location} must name an uppercase environment variable")
    return value


def _validate_optional_environment_reference(value: object, location: str) -> str | None:
    if value is None:
        return None
    return _validate_environment_reference(value, location)


def _flatten_policy(policy: Mapping[str, Any]) -> FlattenedPolicy:
    """Flatten the validated policy into ``Settings`` constructor input.

    The authoritative field specs own the TOML-path-to-Settings-field mapping;
    absent optional keys stay absent so ``Settings`` defaults apply, and
    ``*_env`` keys become pending environment references resolved only by the
    runtime load seam.

    Parameters:
        policy (Mapping[str, Any]): Validated policy table.

    Returns:
        FlattenedPolicy: Settings values plus pending environment references.

    Raises:
        FleetConfigurationError: If a required policy key is missing.
    """

    settings: dict[str, Any] = {}
    environment_references: dict[str, str] = {}
    missing: list[str] = []
    for spec in _FIELD_SPECS:
        value = _lookup_toml(policy, spec.toml_path)
        if value is _MISSING:
            if spec.required_in_policy:
                missing.append(spec.settings_field or spec.toml_path)
            continue
        if spec.environment_reference_for is not None:
            environment_references[spec.environment_reference_for] = value
        else:
            settings[spec.settings_field or spec.toml_path] = value
    if missing:
        raise FleetConfigurationError(f"configuration is missing required setting(s): {', '.join(sorted(missing))}")
    return FlattenedPolicy(settings=settings, environment_references=environment_references)


def _unique_environment_names(*values: str | None) -> tuple[str, ...]:
    """Return non-empty environment names in declaration order without duplicates."""
    return tuple(dict.fromkeys(value for value in values if value))


@dataclass(frozen=True, slots=True)
class PolicyDocument:
    """Validated direct policy without secret/environment resolution."""

    policy: Mapping[str, Any]


def _policy_document_from_mapping(document: Mapping[str, Any]) -> PolicyDocument:
    """Validate the schema-versioned, single Fleet configuration."""
    root = _require_mapping(document, "root")
    config = _require_mapping(root.get("config", {}), "config")
    if {"defaults", "profiles"}.intersection(root) or "default_profile" in config or config.get("schema_version") == 1:
        raise FleetConfigurationError(
            "profile-based Fleet configuration is no longer supported; migrate to config.schema_version = 2: "
            "merge the chosen profile into defaults, move the resulting sections to the root, and remove "
            "defaults, profiles, and config.default_profile. See docs/reference/configuration.md"
        )
    if not isinstance(config.get("schema_version"), int) or config.get("schema_version") != 2:
        raise FleetConfigurationError("config.schema_version must be 2")
    rlm = root.get("rlm")
    if isinstance(rlm, Mapping) and "max_provider_attempts" in rlm:
        raise FleetConfigurationError(
            "rlm.max_provider_attempts has been removed because Fleet did not enforce it; "
            "remove this key. Fleet does not enforce an aggregate provider-attempt or spend cap."
        )
    unknown_config = set(config).difference({"schema_version"})
    if unknown_config:
        raise FleetConfigurationError(f"unknown configuration key(s) at config: {', '.join(sorted(unknown_config))}")
    policy = {key: value for key, value in root.items() if key != "config"}
    _validate_policy_table(policy, "root")
    return PolicyDocument(policy)


def _read_policy_document(path: Path) -> PolicyDocument:
    """Read and validate the non-secret Fleet policy document at ``path``."""
    if not path.is_file():
        raise FleetConfigurationError(f"required Fleet configuration file is missing: {path}")
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FleetConfigurationError(f"could not read Fleet configuration: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise FleetConfigurationError(f"invalid Fleet configuration TOML: {exc}") from exc
    return _policy_document_from_mapping(document)


def load_configuration_environment_contract(path: Path | None = None) -> ConfigurationEnvironmentContract:
    """Return the non-secret environment contract of the single validated policy."""
    policy = _read_policy_document(path or _CONFIG_PATH).policy
    settings = Settings.model_validate(dict(_flatten_policy(policy).settings))

    def table(section: str) -> Mapping[str, Any]:
        return _require_mapping(policy.get(section, {}), section)

    runtime = table("runtime")
    llm = table("llm")
    root = _require_mapping(llm.get("root"), "llm.root")
    sub = _require_mapping(llm.get("sub"), "llm.sub")
    daytona = table("daytona")
    storage = table("storage")
    mlflow = table("mlflow")

    def required_text(value: object, location: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise FleetConfigurationError(f"{location} must be a non-blank string")
        return value

    root_api_key_env = _validate_environment_reference(root.get("api_key_env"), "llm.root.api_key_env")
    sub_api_key_env = _validate_environment_reference(sub.get("api_key_env"), "llm.sub.api_key_env")
    root_base_url_env = _validate_optional_environment_reference(root.get("base_url_env"), "llm.root.base_url_env")
    sub_base_url_env = _validate_optional_environment_reference(sub.get("base_url_env"), "llm.sub.base_url_env")
    daytona_api_key_env = _validate_environment_reference(daytona.get("api_key_env"), "daytona.api_key_env")
    daytona_snapshot_env = _validate_optional_environment_reference(daytona.get("snapshot_env"), "daytona.snapshot_env")
    daytona_child_snapshot_env = _validate_optional_environment_reference(
        daytona.get("child_snapshot_env"), "daytona.child_snapshot_env"
    )
    daytona_org_id_env = _validate_environment_reference(daytona.get("org_id_env"), "daytona.org_id_env")
    database_url_env = _validate_optional_environment_reference(
        storage.get("database_url_env"), "storage.database_url_env"
    )
    mlflow_environment_names = _unique_environment_names(
        *(
            _validate_optional_environment_reference(mlflow.get(f"{field}_env"), f"mlflow.{field}_env")
            for field in (
                "experiment_name",
                "trace_catalog",
                "trace_schema",
                "trace_table_prefix",
                "tracing_sql_warehouse_id",
            )
        )
    )
    provider = "OpenAI Chat Completion"
    return ConfigurationEnvironmentContract(
        runtime_environment=required_text(runtime.get("environment"), "runtime.environment"),
        provider=provider,
        root_model=required_text(root.get("model"), "llm.root.model"),
        sub_model=required_text(sub.get("model"), "llm.sub.model"),
        root_api_key_env=root_api_key_env,
        sub_api_key_env=sub_api_key_env,
        root_base_url_env=root_base_url_env,
        sub_base_url_env=sub_base_url_env,
        root_max_tokens=root.get("max_tokens"),
        sub_max_tokens=sub.get("max_tokens"),
        daytona_api_key_env=daytona_api_key_env,
        daytona_snapshot_env=daytona_snapshot_env,
        daytona_child_snapshot_env=daytona_child_snapshot_env,
        daytona_org_id_env=daytona_org_id_env,
        database_url_env=database_url_env,
        mlflow_tracing_enabled=settings.mlflow_tracing_enabled,
        mlflow_tracking_uri=mlflow.get("tracking_uri"),
        mlflow_environment_names=mlflow_environment_names,
        recursion_enabled=settings.rlm_recursion_enabled,
    )


def _resolve_environment_value(
    name: str | None,
    dotenv: Mapping[str, str | None],
    *,
    dotenv_only: bool = False,
) -> str | None:
    """Resolve one TOML-declared value, optionally requiring repository ``.env``."""
    if name is None:
        return None
    if not dotenv_only:
        value = os.environ.get(name)
        if value is None:
            value = dotenv.get(name)
        value = (value or "").strip()
        return value or None

    dotenv_value = (dotenv.get(name) or "").strip() or None
    process_value = (os.environ.get(name) or "").strip() or None
    if dotenv_value is not None and process_value is not None and dotenv_value != process_value:
        raise FleetConfigurationError(f"environment value {name!r} differs between .env and the process environment")
    # Snapshot identities are safe to resolve from an explicitly declared
    # process variable when the repository .env is absent (for example in a
    # deployment image), while a disagreement fails closed above.
    return dotenv_value or process_value


# Snapshot identities are non-secret operator policy. Prefer the repository
# .env, allow an explicitly TOML-declared process variable when .env is absent,
# and reject disagreement so promotion or rollback cannot select a stale image.
_DOTENV_ONLY_FIELDS: frozenset[str] = frozenset({"daytona_snapshot", "daytona_child_snapshot", "daytona_org_id"})


def load_runtime_settings() -> Settings:
    """Load the single TOML policy and resolve only its declared environment references."""
    dotenv = dotenv_values(".env")
    document = _read_policy_document(_CONFIG_PATH)
    flattened = _flatten_policy(document.policy)

    values: dict[str, Any] = dict(flattened.settings)
    for field_name, environment_name in flattened.environment_references.items():
        resolved = _resolve_environment_value(
            environment_name,
            dotenv,
            dotenv_only=field_name in _DOTENV_ONLY_FIELDS,
        )
        if field_name in _SECRET_RESOLVED_FIELDS:
            values[field_name] = SecretStr(resolved) if resolved is not None else None
        else:
            values[field_name] = resolved
    settings = Settings(**values)
    settings._dotenv_values = {key: value for key, value in dotenv.items() if value is not None}
    return settings


def require_live_execution() -> Settings:
    """Resolve the selected policy and require its live execution switch.

    This is deliberately separate from command invocation: callers still need
    to invoke a live script explicitly, while this single policy check provides
    the repository-wide fail-closed switch for credentialed commands.
    """
    settings = load_runtime_settings()
    if not settings.live_enabled:
        raise FleetConfigurationError("live execution is disabled by runtime.live_enabled=false")
    return settings


def configure_logging(settings: Settings) -> None:
    """Apply Fleet-owned logger levels without configuring handlers or sinks."""
    level = getattr(logging, settings.log_level)
    logging.getLogger("fleet_rlm").setLevel(level)
    logging.getLogger("dspy").setLevel(level)


def redacted_policy_summary(settings: Settings) -> str:
    """Return safe operator diagnostics without resolving any secret values."""
    root = settings.root_lm
    sub = settings.sub_lm
    return (
        f"environment={settings.run_environment} "
        f"root_model={root.model} sub_model={sub.model} "
        f"rlm_iters={settings.rlm_max_iters} "
        f"rlm_llm_calls={settings.rlm_max_llm_calls} "
        f"rlm_verbose={settings.rlm_verbose} log_level={settings.log_level} "
        f"volume={settings.volume_name} "
        f"mlflow_tracing={settings.mlflow_tracing_enabled}"
    )
