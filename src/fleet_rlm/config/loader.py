"""Runtime policy loading and environment resolution for Fleet settings.

Reads the required TOML policy, resolves environment-backed secrets through
the selected profile, and produces the authoritative ``Settings`` instance.
"""

from __future__ import annotations

import logging
import os
import tomllib
from collections.abc import Mapping
from copy import deepcopy
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


@dataclass(frozen=True, slots=True)
class ProfileEnvironmentContract:
    """Non-secret provider and profile facts derived from ``config/fleet.toml``."""

    name: str
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
            self.root_api_key_env,
            self.sub_api_key_env,
            self.root_base_url_env,
            self.sub_base_url_env,
        )

    @property
    def managed_policy_environment_names(self) -> tuple[str, ...]:
        """Return provider plus explicitly required managed-policy environment names."""
        if self.name != "daytona-managed":
            return self.provider_environment_names
        return _unique_environment_names(
            *self.provider_environment_names,
            self.database_url_env,
            *self.mlflow_environment_names,
        )


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


def _validate_policy_table(value: object, location: str, *, allow_partial_llm: bool = False) -> None:
    """
    Validate the structure and environment references in a runtime policy table.

    Parameters:
        value (object): Policy table to validate.
        location (str): Configuration path used in validation errors.
        allow_partial_llm (bool): Whether LLM roles may omit an API key environment reference.

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
            if "api_key_env" in role_table or not allow_partial_llm:
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


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _flatten_policy(policy: Mapping[str, Any]) -> FlattenedPolicy:
    """Flatten one validated profile table into ``Settings`` constructor input.

    The authoritative field specs own the TOML-path-to-Settings-field mapping;
    absent optional keys stay absent so ``Settings`` defaults apply, and
    ``*_env`` keys become pending environment references resolved only by the
    runtime load seam.

    Parameters:
        policy (Mapping[str, Any]): Validated merged policy table for one profile.

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
        raise FleetConfigurationError(f"selected profile is missing required setting(s): {', '.join(sorted(missing))}")
    return FlattenedPolicy(settings=settings, environment_references=environment_references)


def _unique_environment_names(*values: str | None) -> tuple[str, ...]:
    """Return non-empty environment names in declaration order without duplicates."""
    return tuple(dict.fromkeys(value for value in values if value))


@dataclass(frozen=True, slots=True)
class PolicyDocument:
    """Root ``config/fleet.toml`` document without secret/environment resolution."""

    default_profile: str | None
    defaults: Mapping[str, Any]
    profiles: Mapping[str, Any]


def _policy_document_from_mapping(document: Mapping[str, Any]) -> PolicyDocument:
    """Validate and normalize one loaded Fleet policy root table."""
    root = _require_mapping(document, "root")
    allowed_root = {"config", "defaults", "profiles"}
    unknown = set(root).difference(allowed_root)
    if unknown:
        raise FleetConfigurationError(f"unknown configuration key(s): {', '.join(sorted(unknown))}")
    config = _require_mapping(root.get("config", {}), "config")
    if config.get("schema_version") != 1:
        raise FleetConfigurationError("config.schema_version must be 1")
    unknown_config = set(config).difference({"schema_version", "default_profile"})
    if unknown_config:
        raise FleetConfigurationError(f"unknown configuration key(s) at config: {', '.join(sorted(unknown_config))}")
    default_profile = config.get("default_profile")
    if default_profile is not None and not isinstance(default_profile, str):
        raise FleetConfigurationError("config.default_profile must be a string")
    defaults = _require_mapping(root.get("defaults", {}), "defaults")
    profiles = _require_mapping(root.get("profiles", {}), "profiles")
    if not profiles:
        raise FleetConfigurationError("config.profiles must declare at least one profile")
    _validate_policy_table(defaults, "defaults", allow_partial_llm=True)
    if default_profile is not None and default_profile not in profiles:
        raise FleetConfigurationError(f"configured profile does not exist: {default_profile}")
    return PolicyDocument(default_profile, defaults, profiles)


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


def _profile_contract(
    name: str,
    defaults: Mapping[str, Any],
    selected: object,
) -> ProfileEnvironmentContract:
    """
    Builds the non-secret environment contract for a profile after applying its defaults and overrides.

    Parameters:
        name (str): Profile name used to identify configuration locations.
        defaults (Mapping[str, Any]): Default policy values.
        selected (object): Profile-specific policy values.

    Returns:
        ProfileEnvironmentContract: Validated contract containing the profile's runtime,
            model, and environment-reference settings.
    """
    selected_table = _require_mapping(selected, f"profiles.{name}")
    # Profile tables may override only the fields that differ from defaults;
    # the merged policy below remains strict about the complete Root/Sub roles.
    _validate_policy_table(selected_table, f"profiles.{name}", allow_partial_llm=True)
    merged = _deep_merge(defaults, selected_table)
    _validate_policy_table(merged, f"profiles.{name}")

    def table(section: str) -> Mapping[str, Any]:
        return _require_mapping(merged.get(section, {}), f"profiles.{name}.{section}")

    runtime = table("runtime")
    llm = table("llm")
    root = _require_mapping(llm.get("root"), f"profiles.{name}.llm.root")
    sub = _require_mapping(llm.get("sub"), f"profiles.{name}.llm.sub")
    daytona = table("daytona")
    storage = table("storage")
    mlflow = table("mlflow")

    def required_text(value: object, location: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise FleetConfigurationError(f"{location} must be a non-blank string")
        return value

    root_api_key_env = _validate_environment_reference(root.get("api_key_env"), f"profiles.{name}.llm.root.api_key_env")
    sub_api_key_env = _validate_environment_reference(sub.get("api_key_env"), f"profiles.{name}.llm.sub.api_key_env")
    root_base_url_env = _validate_optional_environment_reference(
        root.get("base_url_env"), f"profiles.{name}.llm.root.base_url_env"
    )
    sub_base_url_env = _validate_optional_environment_reference(
        sub.get("base_url_env"), f"profiles.{name}.llm.sub.base_url_env"
    )
    daytona_api_key_env = _validate_environment_reference(
        daytona.get("api_key_env"), f"profiles.{name}.daytona.api_key_env"
    )
    database_url_env = _validate_optional_environment_reference(
        storage.get("database_url_env"), f"profiles.{name}.storage.database_url_env"
    )
    mlflow_environment_names = _unique_environment_names(
        *(
            _validate_optional_environment_reference(mlflow.get(f"{field}_env"), f"profiles.{name}.mlflow.{field}_env")
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
    return ProfileEnvironmentContract(
        name=name,
        runtime_environment=required_text(runtime.get("environment"), f"profiles.{name}.runtime.environment"),
        provider=provider,
        root_model=required_text(root.get("model"), f"profiles.{name}.llm.root.model"),
        sub_model=required_text(sub.get("model"), f"profiles.{name}.llm.sub.model"),
        root_api_key_env=root_api_key_env,
        sub_api_key_env=sub_api_key_env,
        root_base_url_env=root_base_url_env,
        sub_base_url_env=sub_base_url_env,
        root_max_tokens=root.get("max_tokens"),
        sub_max_tokens=sub.get("max_tokens"),
        daytona_api_key_env=daytona_api_key_env,
        database_url_env=database_url_env,
        mlflow_tracing_enabled=bool(mlflow.get("tracing_enabled", False)),
        mlflow_tracking_uri=mlflow.get("tracking_uri"),
        mlflow_environment_names=mlflow_environment_names,
        recursion_enabled=bool(table("rlm").get("recursion_enabled", False)),
    )


def load_profile_environment_contracts(path: Path | None = None) -> tuple[ProfileEnvironmentContract, ...]:
    """Return every profile's provider/environment contract from the TOML policy."""
    document = _read_policy_document(path or _CONFIG_PATH)
    return tuple(_profile_contract(name, document.defaults, selected) for name, selected in document.profiles.items())


def active_profile_contract(path: Path | None = None) -> ProfileEnvironmentContract:
    """Return the contract selected by TOML, never by ambient environment variables."""
    document = _read_policy_document(path or _CONFIG_PATH)
    default_profile = document.default_profile
    if default_profile is None:
        if len(document.profiles) != 1:
            raise FleetConfigurationError("config.default_profile is required when multiple profiles exist")
        default_profile = next(iter(document.profiles))
    return _profile_contract(default_profile, document.defaults, document.profiles[default_profile])


def _resolve_environment_value(name: str | None, dotenv: Mapping[str, str | None]) -> str | None:
    """Resolve one TOML-declared external value; exports win over ``.env``."""
    if name is None:
        return None
    value = os.environ.get(name)
    if value is None:
        value = dotenv.get(name)
    value = (value or "").strip()
    return value or None


def _require_managed_profile_environment_values(
    profile: str,
    flattened: FlattenedPolicy,
    dotenv: Mapping[str, str | None],
) -> None:
    """Fail early when the explicit managed Lakebase/MLflow policy is incomplete."""
    if profile != "daytona-managed":
        return
    # Settings field name -> diagnostic label used when no reference is declared.
    references: tuple[tuple[str, str], ...] = (
        ("database_url", "database_url_env"),
        ("daytona_api_key", "daytona_api_key_env"),
        ("root_llm_api_key_env", "root_llm_api_key_env"),
        ("root_llm_base_url", "root_llm_base_url_env"),
        ("mlflow_experiment_name", "mlflow_experiment_name_env"),
        ("mlflow_trace_catalog", "mlflow_trace_catalog_env"),
        ("mlflow_trace_schema", "mlflow_trace_schema_env"),
        ("mlflow_trace_table_prefix", "mlflow_trace_table_prefix_env"),
        ("mlflow_tracing_sql_warehouse_id", "mlflow_tracing_sql_warehouse_id_env"),
    )
    missing: set[str] = set()
    for field_name, label in references:
        if field_name == "root_llm_api_key_env":
            # The role field stores the environment name directly.
            environment_name: Any = flattened.settings.get(field_name)
        else:
            environment_name = flattened.environment_references.get(field_name)
        if not isinstance(environment_name, str) or not _resolve_environment_value(environment_name, dotenv):
            missing.add(environment_name if isinstance(environment_name, str) else label)
    if missing:
        raise FleetConfigurationError(
            f"selected profile {profile!r} is missing required environment value(s): {', '.join(sorted(missing))}"
        )


def load_runtime_settings() -> Settings:
    """
    Load and validate the runtime settings for the active Fleet profile.

    Returns:
        Settings: Resolved runtime settings, including environment-backed values.

    Raises:
        FleetConfigurationError: If the policy is missing, incomplete, invalid, or unsupported, or
            required environment values are unavailable.
    """
    dotenv = dotenv_values(".env")
    document = _read_policy_document(_CONFIG_PATH)
    defaults = document.defaults
    profiles = document.profiles
    profile = document.default_profile
    if profile is None:
        if len(profiles) == 1:
            profile = next(iter(profiles))
        else:
            raise FleetConfigurationError("config.default_profile is required when multiple profiles exist")
    selected = _require_mapping(profiles[profile], f"profiles.{profile}")
    _validate_policy_table(selected, f"profiles.{profile}", allow_partial_llm=True)
    merged = _deep_merge(defaults, selected)
    _validate_policy_table(merged, f"profiles.{profile}")
    flattened = _flatten_policy(merged)
    _require_managed_profile_environment_values(profile, flattened, dotenv)

    values: dict[str, Any] = dict(flattened.settings)
    for field_name, environment_name in flattened.environment_references.items():
        resolved = _resolve_environment_value(environment_name, dotenv)
        if field_name in _SECRET_RESOLVED_FIELDS:
            values[field_name] = SecretStr(resolved) if resolved is not None else None
        else:
            values[field_name] = resolved
    settings = Settings(**values)
    settings._dotenv_values = {key: value for key, value in dotenv.items() if value is not None}
    settings._active_profile = profile
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


def active_profile(settings: Settings) -> str | None:
    """Return the TOML-selected active profile for the resolved settings."""
    return settings._active_profile


def configure_logging(settings: Settings) -> None:
    """Apply Fleet-owned logger levels without configuring handlers or sinks."""
    level = getattr(logging, settings.log_level)
    logging.getLogger("fleet_rlm").setLevel(level)
    logging.getLogger("dspy").setLevel(level)


def redacted_policy_summary(settings: Settings, *, profile: str) -> str:
    """Return safe operator diagnostics without resolving any secret values."""
    root = settings.root_lm
    sub = settings.sub_lm
    return (
        f"profile={profile} environment={settings.run_environment} "
        f"root_model={root.model} sub_model={sub.model} "
        f"rlm_iters={settings.rlm_max_iters} "
        f"rlm_llm_calls={settings.rlm_max_llm_calls} "
        f"rlm_verbose={settings.rlm_verbose} log_level={settings.log_level} "
        f"volume={settings.volume_name} "
        f"mlflow_tracing={settings.mlflow_tracing_enabled}"
    )
