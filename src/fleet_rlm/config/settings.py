"""Typed settings class and cached process settings loader."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import dspy
from dotenv import dotenv_values
from pydantic import (
    AliasChoices,
    Field,
    PrivateAttr,
    SecretStr,
    TypeAdapter,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from fleet_rlm.config.llm import build_jev_lm, build_judge_lm, judge_environment, list_role_models, resolve_models
from fleet_rlm.config.sources import (
    _SECRET_FIELDS,
    _SETTINGS_SOURCES,
    _TOML_FIELDS,
    DEFAULT_TURN_TIMEOUT_SECONDS,
    MIN_SPLIT_EXAMPLES,
    RETIRED_MLFLOW_ENV,
    MlflowTarget,
    _toml_settings,
    validate_shared_notes_subpath,
    validate_turn_timeout_seconds,
)
from fleet_rlm.models import AdapterName, Endpoint, EnvReference, ModelRoute, ModelType, ResolvedModel, Role
from fleet_rlm.workspace import MAX_WORKSPACE_FILE_BYTES


class FleetSettings(BaseSettings):
    """Process configuration. Secrets are read from environment, never logged."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        validate_by_name=True,
        hide_input_in_errors=True,
    )

    fleet_llm_model: ModelRoute = Field(default="openai/gpt-4o-mini", validation_alias="FLEET_LLM_MODEL")
    fleet_llm_api_base: Endpoint = Field(default="", validation_alias="FLEET_LLM_BASE_URL")
    fleet_llm_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="FLEET_LLM_API_KEY")
    fleet_llm_adapter: AdapterName = "xml"
    fleet_llm_model_type: ModelType = "auto"
    fleet_llm_response_format: Literal["auto", "json_object"] = "auto"
    fleet_llm_api_key_env: EnvReference = ""
    fleet_correctness_model: ModelRoute | Literal[""] = ""
    fleet_judge_api_base: Endpoint | None = None
    fleet_judge_api_key_env: EnvReference | None = None
    fleet_judge_adapter: AdapterName | None = None
    fleet_judge_model_type: ModelType | None = None
    fleet_judge_response_format: Literal["auto", "json_object"] | None = None

    fleet_judge_max_tokens: int | None = Field(default=None, gt=0)
    fleet_judge_temperature: float = 0.0
    fleet_judge_num_retries: int = Field(default=3, ge=0)
    fleet_judge_cache: bool = True

    fleet_llm_max_tokens: int | None = Field(default=None, gt=0)
    fleet_llm_temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    fleet_llm_num_retries: int | None = Field(default=None, ge=0, le=10)
    fleet_llm_cache: bool | None = None
    _resolved_credentials: dict[str, SecretStr] = PrivateAttr(default_factory=dict)
    _resolved_lms: dict[str, ResolvedModel] = PrivateAttr(default_factory=dict)
    _credential_env_file: str | Path | None = PrivateAttr(default=None)
    _configuration_sources: dict[str, str] = PrivateAttr(default_factory=dict)

    def __init__(self, **values: Any) -> None:
        """Load settings while recording which source supplied each explicit value."""
        credential_snapshot = values.pop("_credential_snapshot", {})
        source_snapshot = values.pop("_source_snapshot", None)
        accepted = set(type(self).model_fields)
        for field in type(self).model_fields.values():
            if isinstance(field.validation_alias, str):
                accepted.add(field.validation_alias)
            elif isinstance(field.validation_alias, AliasChoices):
                accepted.update(choice for choice in field.validation_alias.choices if isinstance(choice, str))
        unknown = {key for key in values if key not in accepted and not key.startswith("_")}
        if unknown:
            raise ValueError("Unknown Fleet settings: " + ", ".join(sorted(unknown)))
        sources: dict[str, str] = {}
        token = _SETTINGS_SOURCES.set(sources)
        try:
            super().__init__(**values)
            self._configuration_sources = source_snapshot if source_snapshot is not None else sources
            self._resolved_credentials = dict(credential_snapshot)
            self._credential_env_file = values.get("_env_file", self.model_config.get("env_file"))
            self._resolved_lms = resolve_models(self)
        finally:
            _SETTINGS_SOURCES.reset(token)

    def resolved_model(self, role: Role | Literal["shared"]) -> ResolvedModel:
        """Return a role's validated connection and credential snapshot."""
        return self._resolved_lms[role]

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: Any,
        env_settings: Any,
        dotenv_settings: Any,
        file_secret_settings: Any,
    ) -> tuple[Any, ...]:
        """Order sources as explicit, environment, .env, then TOML, and exclude file secrets.

        Credentials come only from explicit input, the environment, and .env; the TOML map cannot set them.
        """
        del file_secret_settings  # Credentials intentionally use environment/.env only.
        aliases = {name: name for name in cls.model_fields}
        for name, field in cls.model_fields.items():
            alias = field.validation_alias
            if isinstance(alias, str):
                aliases[alias] = name
            elif isinstance(alias, AliasChoices):
                aliases.update({choice: name for choice in alias.choices if isinstance(choice, str)})

        def capture(source: Any, label: str) -> Any:
            def read() -> dict[str, Any]:
                if label in {"environment", "dotenv"}:
                    obsolete = {"llm_base_url", "llm_api_key"} & set(source.env_vars)
                    if obsolete:
                        raise ValueError("Use FLEET_LLM_MODEL, FLEET_LLM_BASE_URL, and FLEET_LLM_API_KEY")
                    retired = sorted(
                        name
                        for name in RETIRED_MLFLOW_ENV
                        if name.lower() in source.env_vars and (label == "dotenv" or name.startswith("FLEET_"))
                    )
                    if retired:
                        raise ValueError(
                            "Retired MLflow settings: "
                            + ", ".join(retired)
                            + "; migrate to FLEET_MLFLOW_LOCAL_* or FLEET_MLFLOW_DATABRICKS_*; "
                            "select the backend with --mlflow local|databricks"
                        )
                values = {aliases.get(key, key): value for key, value in source().items()}
                if label == "dotenv":
                    values = {key: value for key, value in values.items() if key in cls.model_fields}
                sources = _SETTINGS_SOURCES.get()
                if sources is not None:
                    for key in values:
                        sources.setdefault(key, label)
                return values

            return read

        return (
            capture(init_settings, "explicit"),
            capture(env_settings, "environment"),
            capture(dotenv_settings, "dotenv"),
            capture(lambda: _toml_settings(settings_cls), "toml"),
        )

    def configuration(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """Non-secret resolved settings and provenance, safe for CLI/API/trace inspection."""
        overrides = overrides or {}
        values = self.model_dump(mode="json", exclude=_SECRET_FIELDS)
        sources = {name: self._configuration_sources.get(name, "default") for name in values}
        for name, value in overrides.items():
            if name in values:
                values[name] = value
                sources[name] = "session"
        resolved_models = {}
        for role in ("root", "sub", "reflection", "judge"):
            resolved = self.resolved_model(role).model_dump(mode="json", exclude={"api_key"})
            field = f"fleet_{role}_model"
            if role in {"root", "sub"} and field in overrides:
                resolved["model"] = overrides[field]
            resolved_models[role] = resolved
        # Inheritance depends on explicitness, so raw values alone cannot identify
        # effective generation settings. Hash snapshots without reloading credentials.
        material = {"values": values, "resolved_models": resolved_models}
        digest = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {
            "schema_version": 5,
            "digest": digest,
            "values": values,
            "sources": sources,
            "mlflow_target": self.mlflow_target()._asdict(),
            "file_changes_require_restart": True,
        }

    def configuration_source(self, name: str) -> str:
        """Return which source supplied a setting, or `default` when it was not configured."""
        return self._configuration_sources.get(name, "default")

    def for_session(self, saved: dict[str, Any]) -> FleetSettings:
        """Apply only the supported persisted mode/model overrides, never credentials or provider endpoints."""
        overrides = {}
        if "mode" in saved:
            overrides["execution_mode"] = saved["mode"]
        if saved.get("models"):
            overrides.update({f"fleet_{role}_model": saved["models"][role] for role in ("root", "sub")})
        updated = self.model_copy(update=overrides)
        updated._resolved_lms = dict(self._resolved_lms)
        updated._configuration_sources = dict(self._configuration_sources)
        if "execution_mode" in overrides:
            TypeAdapter(Literal["direct", "flex", "rlm", "hybrid"]).validate_python(overrides["execution_mode"])
        for role in ("root", "sub"):
            field = f"fleet_{role}_model"
            if field in overrides:
                route = TypeAdapter(ModelRoute).validate_python(overrides[field])
                updated._resolved_lms[role] = self._resolved_lms[role].model_copy(update={"model": route})
        return updated

    def with_explicit_overrides(self, **values: Any) -> FleetSettings:
        """Validate CLI overrides while retaining the provenance of unchanged settings."""
        unknown = values.keys() - type(self).model_fields.keys()
        if unknown:
            raise ValueError("Unsupported Fleet overrides: " + ", ".join(sorted(unknown)))
        updated = type(self)(
            _env_file=self._credential_env_file,
            _credential_snapshot=self._resolved_credentials,
            _source_snapshot={**self._configuration_sources, **dict.fromkeys(values, "explicit")},
            **{**self.model_dump(), **values},
        )
        updated._configuration_sources = {**self._configuration_sources, **dict.fromkeys(values, "explicit")}
        return updated

    fleet_database_url: str = "postgresql://postgres:postgres@localhost:5432/fleet"
    fleet_database_migration_url: str = Field(
        default="",
        description="Optional PostgreSQL DSN for schema migrations; defaults to the runtime DSN.",
    )
    fleet_database_schema: str = Field(
        default="public",
        description="PostgreSQL schema containing Fleet Lite tables.",
    )
    fleet_lakebase_endpoint: str = Field(
        default="",
        validation_alias="FLEET_LAKEBASE_ENDPOINT",
        description="Lakebase endpoint resource path; enables per-connection Databricks OAuth credentials.",
    )

    fleet_daytona_api_key: str = ""
    fleet_daytona_org_id: str = ""
    fleet_daytona_volume_id: str = ""
    fleet_shared_notes_subpath: str = Field(
        default="shared/learned-notes",
        validation_alias="FLEET_SHARED_NOTES_SUBPATH",
        description="Volume subpath mounted at /learned on root sandboxes; empty disables shared notes.",
    )
    databricks_host: str = Field(default="", validation_alias="DATABRICKS_HOST")
    databricks_token: SecretStr = Field(default=SecretStr(""), validation_alias="DATABRICKS_TOKEN")
    daytona_auto_stop_minutes: int = Field(
        default=30,
        ge=1,
        le=1440,
        validation_alias=AliasChoices("FLEET_DAYTONA_AUTO_STOP_MINUTES", "DAYTONA_AUTO_STOP_MINUTES"),
    )

    fleet_root_model: ModelRoute | Literal[""] = ""
    fleet_root_api_base: Endpoint | None = None
    fleet_root_api_key_env: EnvReference | None = None
    fleet_root_adapter: AdapterName | None = None
    fleet_root_model_type: ModelType | None = None
    fleet_root_response_format: Literal["auto", "json_object"] | None = None

    fleet_sub_model: ModelRoute | Literal[""] = ""
    fleet_sub_api_base: Endpoint | None = None
    fleet_sub_api_key_env: EnvReference | None = None
    fleet_sub_adapter: AdapterName | None = None
    fleet_sub_model_type: ModelType | None = None
    fleet_sub_response_format: Literal["auto", "json_object"] | None = None

    fleet_reflection_model: ModelRoute | Literal[""] = ""
    fleet_reflection_api_base: Endpoint | None = None
    fleet_reflection_api_key_env: EnvReference | None = None
    fleet_reflection_adapter: AdapterName | None = None
    fleet_reflection_model_type: ModelType | None = None
    fleet_reflection_response_format: Literal["auto", "json_object"] | None = None

    fleet_root_max_tokens: int | None = Field(default=16384, ge=1)
    fleet_sub_max_tokens: int | None = Field(default=None, ge=1)
    fleet_reflection_max_tokens: int | None = Field(default=32000, ge=1)
    fleet_root_temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    fleet_sub_temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    fleet_reflection_temperature: float | None = Field(default=1.0, ge=0, le=2, allow_inf_nan=False)
    fleet_root_num_retries: int = Field(default=3, ge=0, le=10)
    fleet_sub_num_retries: int = Field(default=3, ge=0, le=10)
    fleet_reflection_num_retries: int = Field(default=3, ge=0, le=10)
    fleet_root_cache: bool = False
    fleet_sub_cache: bool = False
    fleet_reflection_cache: bool = False
    fleet_judge_model: str = Field(
        default="openai:/system.ai.deepseek-v4-1-flash",
        description="MLflow judge URI; 'openai:/<service-id>' routes through the AI Gateway.",
    )
    typesafe_model: str = Field(default="jev-1.14.0", validation_alias="FLEET_TYPESAFE_MODEL")
    typesafe_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("FLEET_TYPESAFE_API_KEY", "TYPESAFE_API_KEY"),
    )

    max_llm_calls_per_turn: int = Field(
        default=150,
        ge=1,
        le=10_000,
        validation_alias=AliasChoices("FLEET_MAX_LLM_CALLS_PER_TURN", "MAX_LLM_CALLS_PER_TURN"),
    )
    execution_mode: Literal["direct", "rlm", "flex", "hybrid"] = Field(
        default="hybrid", validation_alias="FLEET_EXECUTION_MODE"
    )
    max_agent_depth: int = Field(default=3, ge=0, le=8, validation_alias="FLEET_MAX_AGENT_DEPTH")
    max_child_sandboxes: int = Field(default=8, ge=0, le=64, validation_alias="FLEET_MAX_CHILD_SANDBOXES")
    max_active_children: int = Field(default=4, ge=1, le=16, validation_alias="FLEET_MAX_ACTIVE_CHILDREN")
    turn_timeout_seconds: int = Field(
        default=DEFAULT_TURN_TIMEOUT_SECONDS, validation_alias="FLEET_TURN_TIMEOUT_SECONDS"
    )
    linked_children: bool = Field(default=True, validation_alias="FLEET_LINKED_CHILDREN")
    max_active_sessions: int = Field(
        default=64,
        ge=1,
        le=1024,
        validation_alias=AliasChoices("FLEET_MAX_ACTIVE_SESSIONS", "MAX_ACTIVE_SESSIONS"),
    )
    job_max_tokens: int = Field(
        default=0,
        ge=0,
        description="Generative LM tokens one optimization job may spend; 0 means unlimited.",
        validation_alias="FLEET_JOB_MAX_TOKENS",
    )
    auto_optimize: bool = Field(
        default=False,
        description="Start an optimize job after enough new labels; candidates still wait for human approval.",
        validation_alias="FLEET_AUTO_OPTIMIZE",
    )
    auto_optimize_min_new_labels: int = Field(
        default=5,
        ge=1,
        description="New approved labels since the last finished optimize job that trigger an automatic one.",
        validation_alias="FLEET_AUTO_OPTIMIZE_MIN_NEW_LABELS",
    )
    auto_optimize_max_metric_calls: int | None = Field(
        default=None,
        ge=1,
        description="Search metric calls for automatic jobs; unset keeps the light automatic budget.",
        validation_alias="FLEET_AUTO_OPTIMIZE_MAX_METRIC_CALLS",
    )
    min_split_examples: int = Field(
        default=MIN_SPLIT_EXAMPLES,
        ge=1,
        description="Labelled examples the dev and holdout splits each need before an optimize job may start.",
        validation_alias="FLEET_MIN_SPLIT_EXAMPLES",
    )
    max_upload_bytes: int = Field(
        default=MAX_WORKSPACE_FILE_BYTES,
        ge=1,
        le=MAX_WORKSPACE_FILE_BYTES,
        validation_alias=AliasChoices("FLEET_MAX_UPLOAD_BYTES", "MAX_UPLOAD_BYTES"),
    )
    compiled_program_dir: str = Field(
        default=".fleet/compiled",
        validation_alias=AliasChoices("FLEET_COMPILED_PROGRAM_DIR", "COMPILED_PROGRAM_DIR"),
    )
    local_skills_dir: str = Field(
        default=".fleet/skills",
        validation_alias=AliasChoices("FLEET_LOCAL_SKILLS_DIR", "LOCAL_SKILLS_DIR"),
    )

    mlflow_mode: Literal["local", "databricks"] = Field(default="local", validation_alias="_fleet_selected_mlflow")
    mlflow_local_port: int = Field(default=5001, ge=1, le=65535, validation_alias="FLEET_MLFLOW_LOCAL_PORT")
    mlflow_local_data_dir: str = Field(
        default=".fleet/mlflow", min_length=1, validation_alias="FLEET_MLFLOW_LOCAL_DATA_DIR"
    )
    mlflow_local_experiment_name: str = Field(
        default="fleet-rlm-dev", min_length=1, validation_alias="FLEET_MLFLOW_LOCAL_EXPERIMENT_NAME"
    )
    mlflow_local_eval_experiment_name: str = Field(
        default="fleet-rlm-eval", min_length=1, validation_alias="FLEET_MLFLOW_LOCAL_EVAL_EXPERIMENT_NAME"
    )
    mlflow_local_optimization_experiment_name: str = Field(
        default="fleet-rlm-gepa", min_length=1, validation_alias="FLEET_MLFLOW_LOCAL_OPTIMIZATION_EXPERIMENT_NAME"
    )
    mlflow_databricks_tracking_uri: str = Field(
        default="databricks",
        validation_alias="FLEET_MLFLOW_DATABRICKS_TRACKING_URI",
    )
    mlflow_databricks_experiment_name: str = Field(
        default="/Shared/fleet-rlm-runtime",
        validation_alias="FLEET_MLFLOW_DATABRICKS_EXPERIMENT_NAME",
    )
    mlflow_databricks_eval_experiment_name: str = Field(
        default="/Shared/fleet-rlm-eval", validation_alias="FLEET_MLFLOW_DATABRICKS_EVAL_EXPERIMENT_NAME"
    )
    mlflow_databricks_optimization_experiment_name: str = Field(
        default="/Shared/fleet-rlm-gepa", validation_alias="FLEET_MLFLOW_DATABRICKS_OPTIMIZATION_EXPERIMENT_NAME"
    )
    mlflow_databricks_trace_catalog: str = Field(default="", validation_alias="FLEET_MLFLOW_DATABRICKS_TRACE_CATALOG")
    mlflow_databricks_trace_schema: str = Field(
        default="fleet_observability", validation_alias="FLEET_MLFLOW_DATABRICKS_TRACE_SCHEMA"
    )
    mlflow_databricks_trace_table_prefix: str = Field(
        default="fleet_rlm_runtime", validation_alias="FLEET_MLFLOW_DATABRICKS_TRACE_TABLE_PREFIX"
    )
    mlflow_databricks_tracing_sql_warehouse_id: str = Field(
        default="",
        validation_alias="FLEET_MLFLOW_DATABRICKS_TRACING_SQL_WAREHOUSE_ID",
    )
    mlflow_databricks_optimization_trace_schema: str = Field(
        default="fleet_gepa", validation_alias="FLEET_MLFLOW_DATABRICKS_OPTIMIZATION_TRACE_SCHEMA"
    )
    mlflow_databricks_optimization_trace_table_prefix: str = Field(
        default="fleet_rlm_gepa", validation_alias="FLEET_MLFLOW_DATABRICKS_OPTIMIZATION_TRACE_TABLE_PREFIX"
    )
    mlflow_databricks_eval_trace_table_prefix: str = Field(
        default="fleet_rlm_eval", validation_alias="FLEET_MLFLOW_DATABRICKS_EVAL_TRACE_TABLE_PREFIX"
    )

    host: str = Field(default="127.0.0.1", validation_alias=AliasChoices("FLEET_HOST", "HOST"))
    port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        validation_alias=AliasChoices("FLEET_PORT", "PORT"),
    )
    allow_non_loopback_bind: bool = Field(
        default=False,
        validation_alias=AliasChoices("FLEET_ALLOW_NON_LOOPBACK_BIND", "ALLOW_NON_LOOPBACK_BIND"),
    )

    @field_validator("fleet_database_url")
    @classmethod
    def required_values_are_nonempty(cls, value: str) -> str:
        """Strip whitespace and reject empty required values."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator("turn_timeout_seconds", mode="before")
    @classmethod
    def optional_turn_timeout(cls, value: Any) -> int:
        """Accept 0 or 10..3600 as an integer, allowing numeric text only from the environment."""
        labels = _SETTINGS_SOURCES.get() or {}
        return validate_turn_timeout_seconds(
            value,
            allow_numeric_text=labels.get("turn_timeout_seconds") in {"environment", "dotenv"},
        )

    @field_validator("typesafe_model")
    @classmethod
    def typesafe_model_is_nonempty(cls, value: str) -> str:
        """Strip the TypeSafe route model and reject an empty value."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("TypeSafe route model must not be empty")
        return normalized

    @field_validator("databricks_host", "mlflow_databricks_tracking_uri")
    @classmethod
    def public_urls_do_not_contain_credentials(cls, value: str) -> str:
        """Reject URLs that embed credentials or carry HTTP query strings and fragments."""
        parsed = urlsplit(value if "://" in value else "https://" + value)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Configuration URLs cannot contain credentials; use environment credentials")
        if parsed.scheme in {"http", "https"} and (parsed.query or parsed.fragment):
            raise ValueError("Configuration HTTP URLs cannot contain query credentials or fragments")
        return value

    @field_validator("fleet_shared_notes_subpath")
    @classmethod
    def shared_notes_subpath_is_relative(cls, value: str) -> str:
        """Validate the shared notes Volume subpath as relative."""
        return validate_shared_notes_subpath(value)

    @field_validator("fleet_database_schema")
    @classmethod
    def database_schema_is_safe_identifier(cls, value: str) -> str:
        """Normalize the database schema and require a safe lowercase identifier."""
        normalized = value.strip()
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", normalized):
            raise ValueError("database schema must be a lowercase PostgreSQL identifier")
        return normalized

    @field_validator("fleet_judge_model")
    @classmethod
    def judge_model_is_a_provider_uri(cls, value: str) -> str:
        """Require an MLflow '<provider>:/<model>' judge URI and strip surrounding whitespace."""
        provider, _, name = value.strip().partition(":/")
        if not provider or not name.strip():
            raise ValueError("Judge model must be an MLflow '<provider>:/<model>' URI")
        return value.strip()

    @model_validator(mode="after")
    def databricks_tracking_requires_governed_trace_storage(self) -> FleetSettings:
        """Cross-check database, Lakebase, and Databricks MLflow settings for consistency.

        Databricks tracking needs Unity Catalog trace storage and absolute experiment paths.
        """
        if self.fleet_database_migration_url:
            from psycopg.conninfo import conninfo_to_dict

            runtime_database = conninfo_to_dict(self.fleet_database_url)
            migration_database = conninfo_to_dict(self.fleet_database_migration_url)
            for key, default in (("host", ""), ("port", "5432"), ("dbname", "")):
                if migration_database.get(key, default) != runtime_database.get(key, default):
                    raise ValueError("FLEET_DATABASE_MIGRATION_URL must target the runtime database")
        if self.fleet_lakebase_endpoint:
            if not re.fullmatch(
                r"projects/[a-z0-9-]+/branches/[a-z0-9-]+/endpoints/[a-z0-9-]+",
                self.fleet_lakebase_endpoint,
            ):
                raise ValueError("FLEET_LAKEBASE_ENDPOINT must be a Lakebase endpoint resource path")
            from psycopg.conninfo import conninfo_to_dict

            database = conninfo_to_dict(self.fleet_database_url)
            if database.get("sslmode") != "require" or not database.get("host") or not database.get("user"):
                raise ValueError("OAuth Lakebase DSN requires host, user, and sslmode=require")
            if database.get("password"):
                raise ValueError("Remove the embedded password when FLEET_LAKEBASE_ENDPOINT enables OAuth")
            if self.fleet_database_migration_url:
                migration = conninfo_to_dict(self.fleet_database_migration_url)
                if (
                    migration.get("sslmode") != "require"
                    or not migration.get("host")
                    or not migration.get("user")
                    or migration.get("host") != database.get("host")
                    or migration.get("dbname") != database.get("dbname")
                ):
                    raise ValueError("Lakebase migration DSN must target the same TLS endpoint and database")
                if migration.get("password"):
                    raise ValueError("Remove the embedded password from the Lakebase migration DSN")
        if self.mlflow_mode == "databricks":
            if not self.mlflow_tracking_uri.startswith("databricks"):
                raise ValueError("Databricks mode requires a databricks tracking URI")
            required = (
                self.mlflow_trace_catalog,
                self.mlflow_trace_schema,
                self.mlflow_tracing_sql_warehouse_id,
                self.mlflow_trace_table_prefix,
                self.mlflow_eval_trace_table_prefix,
                self.mlflow_optimization_trace_schema,
                self.mlflow_optimization_trace_table_prefix,
            )
            if not all(item and item.strip() for item in required):
                raise ValueError(
                    "Databricks MLflow tracing requires Unity Catalog catalog, schemas, trace prefixes, "
                    "and SQL warehouse ID"
                )
            if not self.mlflow_tracking_uri.startswith("databricks://") and self.mlflow_tracking_uri != "databricks":
                raise ValueError("Databricks MLflow tracking URI must be 'databricks' or 'databricks://<profile>'")
            if not self.mlflow_experiment_name.startswith("/"):
                raise ValueError("Databricks MLflow experiment names must be absolute workspace paths")
            if not self.mlflow_eval_experiment_name.startswith("/"):
                raise ValueError("Databricks MLflow eval experiment names must be absolute workspace paths")
            if not self.mlflow_optimization_experiment_name.startswith("/"):
                raise ValueError("Databricks MLflow optimization experiment names must be absolute workspace paths")
        return self

    def mlflow_target(self) -> MlflowTarget:
        """Resolve one isolated backend, never borrowing values from the other."""
        prefix = f"mlflow_{self.mlflow_mode}_"
        return MlflowTarget(
            self.mlflow_mode,
            self.mlflow_tracking_uri,
            *(
                getattr(self, prefix + name)
                for name in ("experiment_name", "eval_experiment_name", "optimization_experiment_name")
            ),
        )

    @property
    def mlflow_tracking_uri(self) -> str:
        """Resolved tracking uri for the selected backend."""
        return (
            f"http://127.0.0.1:{self.mlflow_local_port}"
            if self.mlflow_mode == "local"
            else self.mlflow_databricks_tracking_uri
        )

    @property
    def mlflow_experiment_name(self) -> str:
        """Resolved experiment name for the selected backend."""
        return getattr(self, f"mlflow_{self.mlflow_mode}_experiment_name")

    @property
    def mlflow_eval_experiment_name(self) -> str:
        """Resolved eval experiment name for the selected backend."""
        return getattr(self, f"mlflow_{self.mlflow_mode}_eval_experiment_name")

    @property
    def mlflow_optimization_experiment_name(self) -> str:
        """Resolved optimization experiment name for the selected backend."""
        return getattr(self, f"mlflow_{self.mlflow_mode}_optimization_experiment_name")

    @property
    def mlflow_trace_catalog(self) -> str:
        """Resolved trace catalog for the selected backend."""
        return self.mlflow_databricks_trace_catalog if self.mlflow_mode == "databricks" else ""

    @property
    def mlflow_trace_schema(self) -> str:
        """Resolved trace schema for the selected backend."""
        return self.mlflow_databricks_trace_schema if self.mlflow_mode == "databricks" else ""

    @property
    def mlflow_trace_table_prefix(self) -> str:
        """Resolved trace table prefix for the selected backend."""
        return self.mlflow_databricks_trace_table_prefix if self.mlflow_mode == "databricks" else ""

    @property
    def mlflow_tracing_sql_warehouse_id(self) -> str:
        """Resolved tracing sql warehouse id for the selected backend."""
        return self.mlflow_databricks_tracing_sql_warehouse_id if self.mlflow_mode == "databricks" else ""

    @property
    def mlflow_optimization_trace_schema(self) -> str:
        """Resolved optimization trace schema for the selected backend."""
        return self.mlflow_databricks_optimization_trace_schema if self.mlflow_mode == "databricks" else ""

    @property
    def mlflow_optimization_trace_table_prefix(self) -> str:
        """Resolved optimization trace table prefix for the selected backend."""
        return self.mlflow_databricks_optimization_trace_table_prefix if self.mlflow_mode == "databricks" else ""

    @property
    def mlflow_eval_trace_table_prefix(self) -> str:
        """Resolved eval trace table prefix for the selected backend."""
        return self.mlflow_databricks_eval_trace_table_prefix if self.mlflow_mode == "databricks" else ""

    def mlflow_environment(self) -> dict[str, str]:
        """Profile overrides for a subprocess; secrets continue through the existing environment/.env."""
        profile = self.mlflow_mode
        environment = {
            f"FLEET_MLFLOW_{profile.upper()}_{key.upper()}": str(getattr(self, field))
            for key, field in _TOML_FIELDS[f"mlflow.{profile}"].items()
        }
        if profile == "databricks" and self.mlflow_tracking_uri.startswith("databricks://"):
            environment["DATABRICKS_CONFIG_PROFILE"] = self.mlflow_tracking_uri.removeprefix("databricks://")
        return environment

    def local_mlflow_server_environment(self) -> dict[str, str]:
        """Resolve MLflow KEK variables from process env or `.env` for a managed local MLflow server."""
        dotenv: Mapping[str, str | None] | None = None
        resolved: dict[str, str] = {}
        for key in ("MLFLOW_CRYPTO_KEK_PASSPHRASE", "MLFLOW_CRYPTO_KEK_VERSION"):
            if key in os.environ:
                value: str | None = os.environ[key]
            elif self._credential_env_file:
                if dotenv is None:
                    dotenv = dotenv_values(self._credential_env_file)
                value = dotenv.get(key)
            else:
                value = None
            if value:
                resolved[key] = value
        version = resolved.get("MLFLOW_CRYPTO_KEK_VERSION")
        if version is not None:
            try:
                parsed = int(version)
            except ValueError:
                raise ValueError("MLFLOW_CRYPTO_KEK_VERSION must be a positive integer") from None
            if parsed < 1:
                raise ValueError("MLFLOW_CRYPTO_KEK_VERSION must be a positive integer")
        return resolved

    def prepare_mlflow_auth(self) -> None:
        """Make .env credentials available to MLflow's default Databricks SDK authentication."""
        if self.mlflow_mode != "databricks":
            return
        if self.mlflow_tracking_uri.startswith("databricks://"):
            os.environ["DATABRICKS_CONFIG_PROFILE"] = self.mlflow_tracking_uri.removeprefix("databricks://")
        elif self.mlflow_tracking_uri == "databricks":
            if self.databricks_host:
                os.environ["DATABRICKS_HOST"] = self.databricks_host
            if self.databricks_token.get_secret_value():
                os.environ["DATABRICKS_TOKEN"] = self.databricks_token.get_secret_value()

    def mlflow_descriptor(self, *, evaluation: bool = False) -> dict[str, Any]:
        """Safe process destination identity used before reusing a server or submitting evaluation work."""
        target = self.mlflow_target()._asdict()
        target["trace_location"] = None
        target["workspace_host"] = None
        if evaluation:
            target["experiment_name"] = self.mlflow_eval_experiment_name
        if self.mlflow_mode == "databricks":
            host = self.databricks_host if self.mlflow_tracking_uri == "databricks" else ""
            if not host:
                from mlflow.utils.databricks_utils import get_databricks_host_creds

                try:
                    host = get_databricks_host_creds(self.mlflow_tracking_uri).host
                except Exception:
                    raise ValueError("Could not resolve the Databricks tracking workspace identity") from None
            parsed = urlsplit(host if "://" in host else "https://" + host)
            if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("Databricks tracking workspace must be a non-secret host URL")
            target["workspace_host"] = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}".rstrip("/")
            target["trace_location"] = {
                "catalog": self.mlflow_trace_catalog,
                "schema": self.mlflow_trace_schema,
                "table_prefix": self.mlflow_eval_trace_table_prefix if evaluation else self.mlflow_trace_table_prefix,
            }
        return target

    def judge_environment(self) -> dict[str, str]:
        """Environment that points MLflow's `openai:/` scorers at the judge role's own endpoint and key.

        Resolve inherited connections as well as explicit judge overrides, so DSPy and MLflow
        use the same endpoint and credential. Other providers use MLflow's native environment.
        """
        return judge_environment(self)

    def build_jev_lm(self) -> dspy.LM | None:
        """The TypeSafe System One LM, or None when no credential is configured."""
        return build_jev_lm(self)

    def build_judge_lm(self) -> dspy.LM:
        """Build the independently configured DSPy correctness judge."""
        return build_judge_lm(self)

    def list_models(self, scope: str = "", role: Role | Literal["shared"] = "root") -> list[str]:
        """Browse the selected role's explicit compatible endpoint."""
        return list_role_models(self, scope=scope, role=role)


@lru_cache(maxsize=1)
def get_settings(mlflow_mode: Literal["local", "databricks"] | None = None) -> FleetSettings:
    """Load and cache process settings."""
    return FleetSettings(**({"mlflow_mode": mlflow_mode} if mlflow_mode is not None else {}))
