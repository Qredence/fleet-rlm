"""Typed process settings for the Fleet RLM backend.

No clients, engines, LMs, or network access are constructed at import time.
Secrets use ``SecretStr`` so public dumps never expose plaintext values.
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, SecretStr, field_validator, model_validator

from fleet_rlm.snapshot_contract import validate_snapshot_name

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "fleet.toml"
_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _clean_model_provider_service(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("model_provider_service must not be blank")
    return cleaned


class FleetConfigurationError(ValueError):
    """Raised when the required Fleet runtime policy is invalid."""


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


class LLMRoleSettings(BaseModel):
    """Non-secret settings for one explicit Root or Sub Model role."""

    model_config = ConfigDict(extra="forbid")

    model: str
    model_provider_service: str | None = None
    api_key_env: str
    base_url: str | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = None
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    cache: bool = True
    num_retries: int = Field(default=3, ge=0)

    @field_validator("api_key_env")
    @classmethod
    def _validate_api_key_env(cls, value: str) -> str:
        if not _ENVIRONMENT_NAME.fullmatch(value):
            raise ValueError("api_key_env must name an uppercase environment variable")
        return value

    @field_validator("model_provider_service")
    @classmethod
    def _validate_model_provider_service(cls, value: str | None) -> str | None:
        return _clean_model_provider_service(value)


@dataclass(frozen=True, slots=True)
class LLMRoleBundle:
    """Explicitly resolved Root and Sub LM role settings."""

    root: LLMRoleSettings
    sub: LLMRoleSettings


class Settings(BaseModel):
    """Fully resolved Fleet runtime settings.

    Production callers must use :func:`load_runtime_settings`, which derives
    these values from the selected TOML policy and its explicit environment
    references. Direct ``Settings(...)`` construction is for tests and
    injected inventories: it accepts constructor values only.

    This model never scans ambient environment variables, ``.env``, or secret
    files for field names. That would let stale ``FLEET_*`` or unprefixed
    values such as ``DATABASE_URL`` override the selected TOML policy.
    """

    model_config = ConfigDict(extra="ignore")

    app_name: str = Field(default="fleet-rlm")
    daytona_api_key: SecretStr | None = Field(default=None)
    daytona_snapshot: str | None = Field(default=None)
    daytona_org_id: str | None = Field(default=None)
    llm_api_key: SecretStr | None = Field(default=None)
    llm_base_url: str | None = Field(
        default=None,
        description="Optional OpenAI-compatible base URL for dspy.LM",
    )
    llm_max_tokens: int | None = Field(
        default=None,
        ge=1,
        description="Optional output-token limit passed to both DSPy model roles",
    )
    root_model: str = Field(
        default="openai/gpt-4o-mini",
        description="Root LM id for dspy.LM (provider/model)",
    )
    root_llm_model_provider_service: str | None = None
    sub_model: str = Field(
        default="openai/gpt-4o-mini",
        description="Sub LM id for llm_query / llm_query_batched",
    )
    sub_llm_model_provider_service: str | None = None
    database_url: str | None = Field(
        default=None,
        description="Async SQLAlchemy URL (e.g. sqlite+aiosqlite:///:memory: or postgresql+asyncpg://...)",
    )
    volume_name: str = Field(
        default="rlm-volume-dspy",
        description="Daytona Volume name for workspace durable files",
    )
    volume_mount_path: str = Field(
        default="/home/daytona/fleet",
        description="Absolute Sandbox mount path for the workspace Volume",
    )
    run_environment: Literal["daytona"] = Field(default="daytona")
    live_enabled: bool = Field(
        default=True,
        description="Allow explicitly invoked credentialed provider and benchmark commands",
    )
    data_root: str = Field(default=".fleet_rlm")
    max_upload_bytes: int = Field(
        default=10 * 1024 * 1024,
        description="Maximum upload size in bytes",
    )
    max_url_bytes: int = Field(
        default=10 * 1024 * 1024,
        description="Maximum public URL source size in bytes",
    )
    max_artifact_bytes: int = Field(
        default=10 * 1024 * 1024,
        description="Maximum artifact body size in bytes",
    )
    turn_timeout_seconds: int = Field(
        default=1800,
        gt=0,
        description="Turn Timeout in wall-clock seconds for one RLM Turn",
    )
    max_active_daytona_leases: int = Field(
        default=8,
        gt=0,
        le=8,
        description="Daytona Admission bound for process-wide acquiring or active Interpreter Leases",
    )
    rlm_max_iterations: int = Field(default=20, gt=0)
    rlm_max_llm_calls: int = Field(default=50, gt=0)
    rlm_max_output_chars: int = Field(default=10_000, gt=0)
    rlm_max_execution_output_chars: int = Field(default=4_000, gt=0)
    rlm_execution_timeout_s: int = Field(default=120, gt=0)
    rlm_recursion_enabled: bool = False
    rlm_recursion_max_calls: int = Field(default=4, gt=0)
    rlm_recursion_max_prompt_chars: int = Field(default=50_000, gt=0)
    rlm_recursion_child_max_iterations: int = Field(default=8, gt=0)
    rlm_recursion_child_max_llm_calls: int = Field(default=12, gt=0)
    rlm_recursion_child_max_output_chars: int = Field(default=4_000, gt=0)
    rlm_recursion_max_parallel_children: int = Field(default=2, gt=0, le=8)
    rlm_autonomous_memory_categories: tuple[str, ...] = Field(default=())
    run_heartbeat_seconds: int = Field(default=10, gt=0)
    run_stale_after_seconds: int = Field(default=60, gt=0)
    rlm_verbose: bool = True
    log_level: Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"] = "INFO"
    root_llm_api_key_env: str = "FLEET_OPENAI_API_KEY"
    root_llm_base_url: str | None = None
    root_llm_max_tokens: int | None = Field(default=None, ge=1)
    root_llm_temperature: float | None = None
    root_llm_reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    root_llm_cache: bool = True
    root_llm_num_retries: int = Field(default=3, ge=0)
    sub_llm_api_key_env: str = "FLEET_OPENAI_API_KEY"
    sub_llm_base_url: str | None = None
    sub_llm_max_tokens: int | None = Field(default=None, ge=1)
    sub_llm_temperature: float | None = None
    sub_llm_reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    sub_llm_cache: bool = True
    sub_llm_num_retries: int = Field(default=3, ge=0)

    @field_validator("root_llm_model_provider_service", "sub_llm_model_provider_service")
    @classmethod
    def _validate_role_model_provider_service(cls, value: str | None) -> str | None:
        return _clean_model_provider_service(value)

    mlflow_tracing_enabled: bool = Field(
        default=False,
        description="Enable Databricks-backed MLflow DSPy autolog (engineering observability)",
    )
    mlflow_experiment_name: str | None = Field(
        default=None,
        description="MLflow experiment name when tracing is enabled",
    )
    mlflow_tracking_uri: str = Field(
        default="databricks",
        description="MLflow tracking URI selected by the Fleet policy",
    )
    mlflow_expose_trace_id: bool = Field(
        default=True,
        description="When tracing is enabled, surface trace ids on Turn SSE metadata",
    )
    mlflow_async_logging: bool = Field(
        default=True,
        description="Upload MLflow trace data asynchronously when tracing is enabled",
    )
    mlflow_trace_sampling_ratio: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Fraction of Turn traces to sample for MLflow",
    )
    mlflow_trace_content_max_chars: int = Field(
        default=10_000,
        ge=256,
        le=50_000,
        description="Maximum characters retained in one readable MLflow trace payload field",
    )
    mlflow_trace_catalog: str | None = Field(default=None)
    mlflow_trace_schema: str | None = Field(default=None)
    mlflow_trace_table_prefix: str | None = Field(default=None)
    mlflow_tracing_sql_warehouse_id: str | None = Field(default=None)
    _dotenv_values: dict[str, str] = PrivateAttr(default_factory=dict)
    _active_profile: str | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _validate_run_liveness(self) -> Settings:
        if self.run_stale_after_seconds < self.run_heartbeat_seconds * 3:
            raise ValueError("FLEET_RUN_STALE_AFTER_SECONDS must be at least three times FLEET_RUN_HEARTBEAT_SECONDS")
        return self

    @field_validator("rlm_autonomous_memory_categories")
    @classmethod
    def _validate_autonomous_memory_categories(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if type(value) not in (list, tuple):
            raise ValueError("rlm_autonomous_memory_categories must be a category list")
        from fleet_rlm.files.memory_candidates import normalize_memory_candidate_categories

        try:
            return normalize_memory_candidate_categories(value)
        except ValueError as exc:
            raise ValueError("rlm_autonomous_memory_categories contains an invalid Workspace Memory category") from exc

    @field_validator("llm_base_url", mode="before")
    @classmethod
    def _sanitize_llm_base_url(cls, value: object) -> str | None:
        """Only keep real http(s) bases; ignore secrets/comments pasted into .env."""
        if value is None or value == "":
            return None
        text = str(value).strip().strip("'\"")
        if " #" in text:
            text = text.split(" #", 1)[0].rstrip().strip("'\"")
        if not (text.startswith("http://") or text.startswith("https://")):
            return None
        return text.rstrip("/")

    @field_validator("daytona_snapshot", mode="before")
    @classmethod
    def _sanitize_daytona_snapshot(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return validate_snapshot_name(text)
        except ValueError as exc:
            raise ValueError("FLEET_DAYTONA_SNAPSHOT must be immutable and end in -v<positive integer>") from exc

    def llm_role(self, role: Literal["root", "sub"]) -> LLMRoleSettings:
        """Return one explicit Root or Sub role resolved from TOML policy."""
        prefix = f"{role}_llm"
        return LLMRoleSettings(
            model=self.root_model if role == "root" else self.sub_model,
            model_provider_service=getattr(self, f"{prefix}_model_provider_service"),
            api_key_env=getattr(self, f"{prefix}_api_key_env"),
            base_url=getattr(self, f"{prefix}_base_url"),
            max_tokens=getattr(self, f"{prefix}_max_tokens"),
            temperature=getattr(self, f"{prefix}_temperature"),
            reasoning_effort=getattr(self, f"{prefix}_reasoning_effort"),
            cache=getattr(self, f"{prefix}_cache"),
            num_retries=getattr(self, f"{prefix}_num_retries"),
        )

    @property
    def root_lm(self) -> LLMRoleSettings:
        """Resolved Root role settings consumed by model builders and diagnostics."""
        return self.llm_role("root")

    @property
    def sub_lm(self) -> LLMRoleSettings:
        """Resolved Sub role settings consumed by model builders and diagnostics."""
        return self.llm_role("sub")

    @property
    def lm_roles(self) -> LLMRoleBundle:
        """Resolved Root/Sub role bundle; callers no longer chase flat fields."""
        return LLMRoleBundle(root=self.root_lm, sub=self.sub_lm)


_TABLE_KEYS: dict[str, frozenset[str]] = {
    "application": frozenset({"name"}),
    "runtime": frozenset(
        {
            "environment",
            "live_enabled",
            "turn_timeout_seconds",
            "max_active_daytona_leases",
            "heartbeat_seconds",
            "stale_after_seconds",
        }
    ),
    "llm": frozenset({"root", "sub"}),
    "rlm": frozenset(
        {
            "max_iterations",
            "max_llm_calls",
            "max_output_chars",
            "max_execution_output_chars",
            "execution_timeout_s",
            "recursion_enabled",
            "recursion_max_calls",
            "recursion_max_prompt_chars",
            "recursion_child_max_iterations",
            "recursion_child_max_llm_calls",
            "recursion_child_max_output_chars",
            "recursion_max_parallel_children",
            "autonomous_memory_categories",
            "verbose",
        }
    ),
    "storage": frozenset({"data_root", "max_upload_bytes", "max_url_bytes", "max_artifact_bytes", "database_url_env"}),
    "daytona": frozenset({"api_key_env", "snapshot", "org_id", "volume_name", "volume_mount_path"}),
    "logging": frozenset({"level"}),
    "mlflow": frozenset(
        {
            "tracing_enabled",
            "experiment_name",
            "experiment_name_env",
            "tracking_uri",
            "expose_trace_id",
            "async_logging",
            "trace_sampling_ratio",
            "trace_content_max_chars",
            "trace_catalog",
            "trace_catalog_env",
            "trace_schema",
            "trace_schema_env",
            "trace_table_prefix",
            "trace_table_prefix_env",
            "tracing_sql_warehouse_id",
            "tracing_sql_warehouse_id_env",
        }
    ),
}
_ROLE_KEYS = frozenset(
    {
        "model",
        "model_provider_service",
        "api_key_env",
        "base_url",
        "base_url_env",
        "max_tokens",
        "temperature",
        "reasoning_effort",
        "cache",
        "num_retries",
    }
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
        extras = set(llm).difference(_TABLE_KEYS["llm"])
        if extras:
            raise FleetConfigurationError(
                f"unknown configuration key(s) at {location}.llm: {', '.join(sorted(extras))}"
            )
        for role, role_value in llm.items():
            role_table = _require_mapping(role_value, f"{location}.llm.{role}")
            role_extras = set(role_table).difference(_ROLE_KEYS)
            if role_extras:
                raise FleetConfigurationError(
                    f"unknown configuration key(s) at {location}.llm.{role}: {', '.join(sorted(role_extras))}"
                )
            provider_service = role_table.get("model_provider_service")
            if provider_service is not None and (not isinstance(provider_service, str) or not provider_service.strip()):
                raise FleetConfigurationError(
                    f"{location}.llm.{role}.model_provider_service must be a non-blank string"
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
        if name == "storage":
            _validate_optional_environment_reference(
                _require_mapping(child, f"{location}.storage").get("database_url_env"),
                f"{location}.storage.database_url_env",
            )
        elif name == "daytona":
            _validate_optional_environment_reference(
                _require_mapping(child, f"{location}.daytona").get("api_key_env"),
                f"{location}.daytona.api_key_env",
            )
        elif name == "mlflow":
            mlflow = _require_mapping(child, f"{location}.mlflow")
            for key in (
                "experiment_name",
                "trace_catalog",
                "trace_schema",
                "trace_table_prefix",
                "tracing_sql_warehouse_id",
            ):
                reference = f"{key}_env"
                if key in mlflow and reference in mlflow:
                    raise FleetConfigurationError(f"{location}.mlflow cannot define both {key} and {reference}")
                _validate_optional_environment_reference(mlflow.get(reference), f"{location}.mlflow.{reference}")


def _validate_environment_reference(value: object, location: str) -> str:
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


def _flatten_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    """
    Flatten validated nested policy sections into the field names required by `Settings`.

    Parameters:
        policy (Mapping[str, Any]): Validated policy configuration containing application, runtime, RLM,
            storage, Daytona, logging, LLM, and MLflow sections.

    Returns:
        dict[str, Any]: Settings field values with applicable defaults applied.

    Raises:
        FleetConfigurationError: If required settings are missing.
    """

    def table(name: str) -> Mapping[str, Any]:
        return _require_mapping(policy.get(name, {}), name)

    application = table("application")
    runtime = table("runtime")
    rlm = table("rlm")
    storage = table("storage")
    daytona = table("daytona")
    log = table("logging")
    llm = table("llm")
    mlflow = table("mlflow")
    values: dict[str, Any] = {
        "app_name": application.get("name"),
        "run_environment": runtime.get("environment"),
        "live_enabled": runtime.get("live_enabled", True),
        "turn_timeout_seconds": runtime.get("turn_timeout_seconds"),
        "max_active_daytona_leases": runtime.get("max_active_daytona_leases"),
        "run_heartbeat_seconds": runtime.get("heartbeat_seconds"),
        "run_stale_after_seconds": runtime.get("stale_after_seconds"),
        "rlm_max_iterations": rlm.get("max_iterations"),
        "rlm_max_llm_calls": rlm.get("max_llm_calls"),
        "rlm_max_output_chars": rlm.get("max_output_chars"),
        "rlm_max_execution_output_chars": rlm.get("max_execution_output_chars"),
        "rlm_execution_timeout_s": rlm.get("execution_timeout_s"),
        "rlm_recursion_enabled": rlm.get("recursion_enabled", False),
        "rlm_recursion_max_calls": rlm.get("recursion_max_calls", 4),
        "rlm_recursion_max_prompt_chars": rlm.get("recursion_max_prompt_chars", 50_000),
        "rlm_recursion_child_max_iterations": rlm.get("recursion_child_max_iterations", 8),
        "rlm_recursion_child_max_llm_calls": rlm.get("recursion_child_max_llm_calls", 12),
        "rlm_recursion_child_max_output_chars": rlm.get("recursion_child_max_output_chars", 4_000),
        "rlm_recursion_max_parallel_children": rlm.get("recursion_max_parallel_children", 2),
        "rlm_autonomous_memory_categories": rlm.get("autonomous_memory_categories", ()),
        "rlm_verbose": rlm.get("verbose"),
        "data_root": storage.get("data_root"),
        "max_upload_bytes": storage.get("max_upload_bytes"),
        "max_url_bytes": storage.get("max_url_bytes"),
        "max_artifact_bytes": storage.get("max_artifact_bytes"),
        "database_url_env": storage.get("database_url_env"),
        "daytona_api_key_env": daytona.get("api_key_env"),
        "daytona_snapshot": daytona.get("snapshot"),
        "daytona_org_id": daytona.get("org_id"),
        "volume_name": daytona.get("volume_name"),
        "volume_mount_path": daytona.get("volume_mount_path"),
        "log_level": log.get("level"),
    }
    if "tracing_enabled" in mlflow:
        values["mlflow_tracing_enabled"] = mlflow["tracing_enabled"]
    for key, settings_field in (
        ("experiment_name", "mlflow_experiment_name"),
        ("trace_catalog", "mlflow_trace_catalog"),
        ("trace_schema", "mlflow_trace_schema"),
        ("trace_table_prefix", "mlflow_trace_table_prefix"),
        ("tracing_sql_warehouse_id", "mlflow_tracing_sql_warehouse_id"),
    ):
        if key in mlflow:
            values[settings_field] = mlflow[key]
        if f"{key}_env" in mlflow:
            values[f"{settings_field}_env"] = mlflow[f"{key}_env"]
    if "tracking_uri" in mlflow:
        values["mlflow_tracking_uri"] = mlflow["tracking_uri"]
    if "expose_trace_id" in mlflow:
        values["mlflow_expose_trace_id"] = mlflow["expose_trace_id"]
    if "async_logging" in mlflow:
        values["mlflow_async_logging"] = mlflow["async_logging"]
    if "trace_sampling_ratio" in mlflow:
        values["mlflow_trace_sampling_ratio"] = mlflow["trace_sampling_ratio"]
    if "trace_content_max_chars" in mlflow:
        values["mlflow_trace_content_max_chars"] = mlflow["trace_content_max_chars"]
    for role in ("root", "sub"):
        role_values = _require_mapping(llm.get(role, {}), f"llm.{role}")
        values[f"{role}_model"] = role_values.get("model")
        values[f"{role}_llm_model_provider_service"] = role_values.get("model_provider_service")
        values[f"{role}_llm_api_key_env"] = role_values.get("api_key_env")
        values[f"{role}_llm_base_url"] = role_values.get("base_url")
        values[f"{role}_llm_base_url_env"] = role_values.get("base_url_env")
        values[f"{role}_llm_max_tokens"] = role_values.get("max_tokens")
        values[f"{role}_llm_temperature"] = role_values.get("temperature")
        values[f"{role}_llm_reasoning_effort"] = role_values.get("reasoning_effort")
        values[f"{role}_llm_cache"] = role_values.get("cache", True)
        values[f"{role}_llm_num_retries"] = role_values.get("num_retries", 3)
    optional = {
        "database_url_env",
        "daytona_api_key_env",
        "daytona_snapshot",
        "daytona_org_id",
        "root_llm_model_provider_service",
        "sub_llm_model_provider_service",
        "root_llm_base_url",
        "sub_llm_base_url",
        "root_llm_base_url_env",
        "sub_llm_base_url_env",
        "mlflow_experiment_name_env",
        "mlflow_trace_catalog_env",
        "mlflow_trace_schema_env",
        "mlflow_trace_table_prefix_env",
        "mlflow_tracing_sql_warehouse_id_env",
        "root_llm_max_tokens",
        "sub_llm_max_tokens",
        "root_llm_temperature",
        "sub_llm_temperature",
        "root_llm_reasoning_effort",
        "sub_llm_reasoning_effort",
        "root_llm_cache",
        "sub_llm_cache",
        "root_llm_num_retries",
        "sub_llm_num_retries",
    }
    missing = sorted(key for key, value in values.items() if value is None and key not in optional)
    if missing:
        raise FleetConfigurationError(f"selected profile is missing required setting(s): {', '.join(missing)}")
    return values


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
    """Build one non-secret contract from defaults merged with a selected profile."""
    selected_table = _require_mapping(selected, f"profiles.{name}")
    _validate_policy_table(selected_table, f"profiles.{name}")
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
    key_pair = (root_api_key_env, root_base_url_env)
    provider = {
        ("FLEET_OPENCODE_GO_API_KEY", "FLEET_OPENCODE_GO_BASE_URL"): "OpenCode Go",
        ("DATABRICKS_TOKEN", "FLEET_DATABRICKS_AI_GATEWAY_BASE_URL"): "Databricks AI Gateway",
    }.get(key_pair, "custom configured gateway")
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
    values: Mapping[str, Any],
    dotenv: Mapping[str, str | None],
) -> None:
    """Fail early when the explicit managed Lakebase/MLflow policy is incomplete."""
    if profile != "daytona-managed":
        return
    references = (
        "database_url_env",
        "daytona_api_key_env",
        "root_llm_api_key_env",
        "root_llm_base_url_env",
        "mlflow_experiment_name_env",
        "mlflow_trace_catalog_env",
        "mlflow_trace_schema_env",
        "mlflow_trace_table_prefix_env",
        "mlflow_tracing_sql_warehouse_id_env",
    )
    missing: set[str] = set()
    for field_name in references:
        environment_name = values.get(field_name)
        if not isinstance(environment_name, str) or not _resolve_environment_value(environment_name, dotenv):
            missing.add(environment_name if isinstance(environment_name, str) else field_name)
    if missing:
        raise FleetConfigurationError(
            f"selected profile {profile!r} is missing required environment value(s): {', '.join(missing)}"
        )


def load_runtime_settings() -> Settings:
    """
    Load and validate the active Fleet runtime configuration.

    Returns:
        Settings: The resolved runtime settings for the selected profile.

    Raises:
        FleetConfigurationError: If the configuration file is missing or contains invalid, incomplete,
            or unsupported settings.
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
    _validate_policy_table(selected, f"profiles.{profile}")
    values = _flatten_policy(_deep_merge(defaults, selected))
    _require_managed_profile_environment_values(profile, values, dotenv)

    database_url_env = values.pop("database_url_env", None)
    daytona_api_key_env = values.pop("daytona_api_key_env", None)
    values["database_url"] = _resolve_environment_value(database_url_env, dotenv)
    daytona_api_key = _resolve_environment_value(daytona_api_key_env, dotenv)
    values["daytona_api_key"] = SecretStr(daytona_api_key) if daytona_api_key is not None else None
    for settings_field in (
        "root_llm_base_url",
        "sub_llm_base_url",
        "mlflow_experiment_name",
        "mlflow_trace_catalog",
        "mlflow_trace_schema",
        "mlflow_trace_table_prefix",
        "mlflow_tracing_sql_warehouse_id",
    ):
        reference = values.pop(f"{settings_field}_env", None)
        if reference is not None:
            values[settings_field] = _resolve_environment_value(reference, dotenv)
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
        f"rlm_iterations={settings.rlm_max_iterations} "
        f"rlm_llm_calls={settings.rlm_max_llm_calls} "
        f"rlm_verbose={settings.rlm_verbose} log_level={settings.log_level} "
        f"volume={settings.volume_name} "
        f"mlflow_tracing={settings.mlflow_tracing_enabled}"
    )
