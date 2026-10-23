"""Typed settings and model construction for Fleet-RLM Lite."""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import tomllib
from functools import lru_cache
from itertools import islice
from pathlib import Path
from typing import Any, Literal, NamedTuple
from urllib.parse import urlsplit

import dspy
from pydantic import (
    AliasChoices,
    Field,
    PrivateAttr,
    SecretStr,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

_SECRET_FIELDS = {
    "fleet_database_url",
    "fleet_database_migration_url",
    "fleet_daytona_api_key",
    "databricks_token",
    "typesafe_api_key",
}
_TOML_FIELDS = {
    "runtime": {
        "execution_mode": "execution_mode",
        "turn_timeout_seconds": "turn_timeout_seconds",
        "max_llm_calls_per_turn": "max_llm_calls_per_turn",
        "max_agent_depth": "max_agent_depth",
        "max_child_sandboxes": "max_child_sandboxes",
        "max_active_children": "max_active_children",
        "max_active_sessions": "max_active_sessions",
        "linked_children": "linked_children",
    },
    "gateway": {"host": "databricks_host", "base_url": "fleet_llm_base_url"},
    "decision": {"model": "typesafe_model"},
    "daytona": {
        "snapshot": "fleet_daytona_snapshot",
        "org_id": "fleet_daytona_org_id",
        "volume_id": "fleet_daytona_volume_id",
        "auto_stop_minutes": "daytona_auto_stop_minutes",
        "root_auto_delete_minutes": "daytona_root_auto_delete_minutes",
        "shared_notes_subpath": "fleet_shared_notes_subpath",
    },
    "storage": {
        "max_upload_bytes": "max_upload_bytes",
        "compiled_program_dir": "compiled_program_dir",
        "local_skills_dir": "local_skills_dir",
        "lakebase_endpoint": "fleet_lakebase_endpoint",
        "database_schema": "fleet_database_schema",
    },
    "mlflow.local": {
        key: f"mlflow_local_{key}"
        for key in ("port", "data_dir", "experiment_name", "eval_experiment_name", "optimization_experiment_name")
    },
    "mlflow.databricks": {
        key: f"mlflow_databricks_{key}"
        for key in (
            "tracking_uri",
            "experiment_name",
            "eval_experiment_name",
            "optimization_experiment_name",
            "trace_catalog",
            "trace_schema",
            "trace_table_prefix",
            "tracing_sql_warehouse_id",
            "optimization_trace_schema",
            "optimization_trace_table_prefix",
            "eval_trace_table_prefix",
        )
    },
    "evaluation": {"judge_model": "fleet_judge_model"},
    "server": {"host": "host", "port": "port", "allow_non_loopback_bind": "allow_non_loopback_bind"},
}
for _role in ("root", "sub", "reflection"):
    _TOML_FIELDS[f"llm.{_role}"] = {
        name: f"fleet_{_role}_{name}" for name in ("model", "max_tokens", "temperature", "num_retries", "cache")
    }
RETIRED_MLFLOW_ENV = {
    "FLEET_MLFLOW_MODE",
    "MLFLOW_TRACKING_URI",
    "MLFLOW_EXPERIMENT_NAME",
    *(f"FLEET_MLFLOW_{key.upper()}" for key in _TOML_FIELDS["mlflow.databricks"]),
}


class MlflowTarget(NamedTuple):
    """Where traces, eval runs, and optimizer runs go for the selected MLflow mode."""

    mode: str
    tracking_uri: str
    experiment_name: str
    eval_experiment_name: str
    optimization_experiment_name: str


_SETTINGS_SOURCES: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "fleet_settings_sources", default=None
)


def _toml_settings(settings_cls: type[BaseSettings]) -> dict[str, Any]:
    explicit = "FLEET_CONFIG" in os.environ
    path = Path(os.environ.get("FLEET_CONFIG", "config/fleet.toml"))
    if not path.is_file():
        if explicit or path.exists():
            raise ValueError("Selected Fleet configuration must be an existing TOML file")
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        raise ValueError("Fleet configuration could not be read as valid TOML") from None
    schema = data.pop("config", None)
    if schema != {"schema_version": 4} or type(schema.get("schema_version")) is not int:
        raise ValueError(
            "Fleet TOML requires [config] schema_version = 4; "
            "migrate [mlflow] to [mlflow.local] and [mlflow.databricks]"
        )
    tables = dict(data)
    for parent in ("llm", "mlflow"):
        if parent not in tables:
            continue
        children = tables.pop(parent)
        if not isinstance(children, dict):
            raise ValueError(f"Fleet TOML {parent} must be a table")
        tables.update({f"{parent}.{name}": values for name, values in children.items()})
    result = {}
    for table, values in tables.items():
        if table not in _TOML_FIELDS or not isinstance(values, dict):
            raise ValueError(f"Unsupported Fleet TOML table: {table}")
        for key, value in values.items():
            field = _TOML_FIELDS[table].get(key)
            if field is None:
                raise ValueError(f"Unsupported or environment-only Fleet TOML setting: {table}.{key}")
            try:
                TypeAdapter(settings_cls.model_fields[field].rebuild_annotation()).validate_python(value, strict=True)
            except ValidationError:
                raise ValueError(f"Invalid Fleet TOML setting: {table}.{key}") from None
            result[field] = value
    return result


# Seven days: how long a stopped root sandbox is kept before Daytona deletes it, unless configured.
DEFAULT_ROOT_AUTO_DELETE_MINUTES = 7 * 24 * 60


def validate_root_auto_delete_minutes(value: int) -> int:
    """Return a root sandbox auto-delete interval: -1 (never) or a positive number of minutes."""
    if type(value) is not int or (value != -1 and value < 1):
        raise ValueError("root auto-delete minutes must be -1 (never) or a positive number of minutes")
    return value


def validate_shared_notes_subpath(value: str) -> str:
    """Accept '' (disabled) or a relative Daytona Volume subpath, mirroring the provider's subpath rules."""
    if value and (value.startswith("/") or value.endswith("/") or ".." in value or "//" in value):
        raise ValueError("shared notes subpath must be relative, without '..', '//', or a trailing '/'")
    return value


class FleetSettings(BaseSettings):
    """Process configuration. Secrets are read from environment, never logged."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_by_name=True,
        hide_input_in_errors=True,
    )

    _configuration_sources: dict[str, str] = PrivateAttr(default_factory=dict)

    def __init__(self, **values: Any) -> None:
        """Load settings while recording which source supplied each explicit value."""
        sources: dict[str, str] = {}
        token = _SETTINGS_SOURCES.set(sources)
        try:
            super().__init__(**values)
            self._configuration_sources = sources
        finally:
            _SETTINGS_SOURCES.reset(token)

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
        values = self.model_dump(mode="json", exclude=_SECRET_FIELDS)
        sources = {name: self._configuration_sources.get(name, "default") for name in values}
        for name, value in (overrides or {}).items():
            if name in values:
                values[name] = value
                sources[name] = "session"
        digest = hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return {
            "schema_version": 4,
            "digest": digest,
            "values": values,
            "sources": sources,
            "mlflow_target": self.mlflow_target()._asdict(),
            "file_changes_require_restart": True,
        }

    def with_explicit_overrides(self, **values: Any) -> FleetSettings:
        """Validate CLI overrides while retaining the provenance of unchanged settings."""
        unknown = values.keys() - type(self).model_fields.keys()
        if unknown:
            raise ValueError("Unsupported Fleet overrides: " + ", ".join(sorted(unknown)))
        updated = type(self)(**{**self.model_dump(), **values})
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
    fleet_daytona_snapshot: str = "fleet-rlm-base"
    databricks_host: str = Field(default="", validation_alias="DATABRICKS_HOST")
    databricks_token: SecretStr = Field(default=SecretStr(""), validation_alias="DATABRICKS_TOKEN")
    fleet_llm_base_url: str = Field(default="", validation_alias="FLEET_LLM_BASE_URL")
    daytona_auto_stop_minutes: int = Field(
        default=30,
        ge=1,
        le=1440,
        validation_alias=AliasChoices("FLEET_DAYTONA_AUTO_STOP_MINUTES", "DAYTONA_AUTO_STOP_MINUTES"),
    )
    daytona_root_auto_delete_minutes: int = Field(
        default=DEFAULT_ROOT_AUTO_DELETE_MINUTES,
        ge=-1,
        le=365 * 24 * 60,
        validation_alias="FLEET_DAYTONA_ROOT_AUTO_DELETE_MINUTES",
        description=(
            "Minutes a stopped, non-disposable root sandbox is kept before Daytona deletes it; -1 keeps it forever. "
            "Session state lives on the Volume, so a deleted root sandbox is rebuilt from the snapshot."
        ),
    )

    fleet_root_model: str = "openai/system.ai.deepseek-v4-1-flash"
    fleet_sub_model: str = "openai/system.ai.claude-sonnet-5-5"
    fleet_reflection_model: str = "openai/system.ai.claude-haiku-5-5"
    fleet_root_max_tokens: int | None = Field(default=None, ge=1)
    fleet_sub_max_tokens: int | None = Field(default=None, ge=1)
    fleet_reflection_max_tokens: int | None = Field(default=None, ge=1)
    fleet_root_temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    fleet_sub_temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    fleet_reflection_temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
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
    execution_mode: Literal["rlm", "flex", "hybrid"] = Field(default="hybrid", validation_alias="FLEET_EXECUTION_MODE")
    max_agent_depth: int = Field(default=3, ge=0, le=8, validation_alias="FLEET_MAX_AGENT_DEPTH")
    max_child_sandboxes: int = Field(default=8, ge=0, le=64, validation_alias="FLEET_MAX_CHILD_SANDBOXES")
    max_active_children: int = Field(default=4, ge=1, le=16, validation_alias="FLEET_MAX_ACTIVE_CHILDREN")
    turn_timeout_seconds: int = Field(default=900, ge=10, le=3600, validation_alias="FLEET_TURN_TIMEOUT_SECONDS")
    # A Turn whose sandbox is idle and whose model/tool callbacks have been silent this long is stopped.
    turn_stall_seconds: int = Field(default=300, ge=10, le=3600, validation_alias="FLEET_TURN_STALL_SECONDS")
    linked_children: bool = Field(default=True, validation_alias="FLEET_LINKED_CHILDREN")
    max_active_sessions: int = Field(
        default=64,
        ge=1,
        le=1024,
        validation_alias=AliasChoices("FLEET_MAX_ACTIVE_SESSIONS", "MAX_ACTIVE_SESSIONS"),
    )
    max_upload_bytes: int = Field(
        default=25_000_000,
        ge=1,
        le=25_000_000,
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
    mlflow_databricks_trace_catalog: str = Field(
        default="uscentral", validation_alias="FLEET_MLFLOW_DATABRICKS_TRACE_CATALOG"
    )
    mlflow_databricks_trace_schema: str = Field(
        default="fleet_observability", validation_alias="FLEET_MLFLOW_DATABRICKS_TRACE_SCHEMA"
    )
    mlflow_databricks_trace_table_prefix: str = Field(
        default="fleet_rlm_runtime", validation_alias="FLEET_MLFLOW_DATABRICKS_TRACE_TABLE_PREFIX"
    )
    mlflow_databricks_tracing_sql_warehouse_id: str = Field(
        default="4d07bd43a3ddfff2",
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

    @field_validator(
        "fleet_database_url",
        "fleet_daytona_snapshot",
        "fleet_root_model",
        "fleet_sub_model",
        "fleet_reflection_model",
    )
    @classmethod
    def required_values_are_nonempty(cls, value: str) -> str:
        """Strip whitespace and reject empty required values."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator("fleet_root_model", "fleet_sub_model", "fleet_reflection_model")
    @classmethod
    def model_routes_use_native_gateway(cls, value: str) -> str:
        """Require runtime models to use the native 'openai/<service-id>' route."""
        if not value.startswith("openai/") or not value.removeprefix("openai/").strip():
            raise ValueError("Runtime models require an OpenAI-compatible 'openai/<service-id>' route")
        return value

    @field_validator("fleet_llm_base_url")
    @classmethod
    def gateway_override_uses_https(cls, value: str) -> str:
        """Accept an empty gateway override, or an HTTPS workspace URL."""
        parsed = urlsplit(value)
        if value and (parsed.scheme != "https" or not parsed.netloc):
            raise ValueError("Gateway base URL must be an HTTPS workspace URL")
        return value

    @field_validator("typesafe_model")
    @classmethod
    def typesafe_model_is_nonempty(cls, value: str) -> str:
        """Strip the TypeSafe route model and reject an empty value."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("TypeSafe route model must not be empty")
        return normalized

    @field_validator("databricks_host", "fleet_llm_base_url", "mlflow_databricks_tracking_uri")
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

    @field_validator("daytona_root_auto_delete_minutes")
    @classmethod
    def root_auto_delete_is_not_immediate(cls, value: int) -> int:
        """Refuse 0, which would make Daytona delete the warm root sandbox on every auto-stop."""
        return validate_root_auto_delete_minutes(value)

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
        return {
            f"FLEET_MLFLOW_{profile.upper()}_{key.upper()}": str(getattr(self, field))
            for key, field in _TOML_FIELDS[f"mlflow.{profile}"].items()
        }

    def prepare_mlflow_auth(self) -> None:
        """Make .env credentials available to MLflow's default Databricks SDK authentication."""
        if self.mlflow_mode == "databricks" and self.mlflow_tracking_uri == "databricks":
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
        """Credentials MLflow's OpenAI judge provider reads from the environment for gateway judges."""
        if not self.fleet_judge_model.startswith("openai:/"):
            return {}
        return {"OPENAI_API_KEY": self._gateway_api_key(), "OPENAI_API_BASE": self._gateway_base_url()}

    def build_lms(self) -> tuple[dspy.LM, dspy.LM, dspy.LM, Any | None]:
        """Create role-specific LMs without mutating process-global DSPy settings."""
        root_lm = self._build_lm(self.fleet_root_model, "root")
        sub_lm = self._build_lm(self.fleet_sub_model, "sub")
        reflection_lm = self._build_lm(self.fleet_reflection_model, "reflection")
        return root_lm, sub_lm, reflection_lm, self.build_jev_lm()

    def build_jev_lm(self) -> Any | None:
        """The TypeSafe System One client, or None when no credential is configured.

        Raises:
            RuntimeError: A credential is configured but the DSPy TypeSafe import fails.

        TypeSafe client construction errors propagate.
        """
        if not self.typesafe_api_key:
            return None
        try:
            from dspy.experimental import TypeSafe
        except ImportError as exc:
            raise RuntimeError("TypeSafe routing requires the DSPy 'typesafe' extra") from exc
        return TypeSafe(self.typesafe_model, api_key=self.typesafe_api_key)

    def build_judge_lm(self) -> dspy.LM:
        """The DSPy twin of FLEET_JUDGE_MODEL: temperature zero and cached for consistent repeated judging."""
        if not self.fleet_judge_model.startswith("openai:/"):
            raise ValueError("The DSPy judge needs FLEET_JUDGE_MODEL as an 'openai:/<service-id>' gateway route")
        return dspy.LM(
            self.fleet_judge_model.replace("openai:/", "openai/", 1),
            api_base=self._gateway_base_url(),
            api_key=self._gateway_api_key(),
            engine="lm15",
            temperature=0.0,
            cache=True,
            num_retries=self.fleet_reflection_num_retries,
        )

    def list_models(self, scope: str = "system.ai") -> list[str]:
        """Discover authorized chat-capable Unity Gateway services, not inference /models."""
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.core import Config

        if not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", scope):
            raise ValueError("Model scope must be catalog.schema")
        host = self.databricks_host.strip().rstrip("/")
        if not host:
            parsed = urlsplit(self.fleet_llm_base_url)
            host = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""
        if not host or not self.databricks_token.get_secret_value():
            raise ValueError("Model discovery requires a workspace host and token")
        client = WorkspaceClient(
            config=Config(
                host=host,
                token=self.databricks_token.get_secret_value(),
                auth_type="pat",
                http_timeout_seconds=20,
                retry_timeout_seconds=20,
            )
        )
        return sorted(
            {
                "openai/" + service.name.removeprefix("model-services/")
                for service in islice(client.ai_gateway.list_model_services(parent=f"schemas/{scope}"), 1000)
                if service.name and "mlflow/v1/chat/completions" in (service.supported_api_types or [])
            }
        )

    def _build_lm(self, model: str, role: str = "root") -> dspy.LM:
        """Use native DSPy routing only; never fall back to LiteLLM."""
        if not model.startswith("openai/") or not model.removeprefix("openai/").strip():
            raise ValueError(
                f"Fleet model {model!r} must use an OpenAI-compatible 'openai/<service-id>' route; "
                "refusing to fall back to another inference backend"
            )

        options = {
            name: getattr(self, f"fleet_{role}_{name}")
            for name in ("max_tokens", "temperature", "num_retries", "cache")
        }
        return dspy.LM(
            model,
            api_base=self._gateway_base_url(),
            api_key=self._gateway_api_key(),
            engine="lm15",
            **{key: value for key, value in options.items() if value is not None},
        )

    def _gateway_base_url(self) -> str:
        base_url = self.fleet_llm_base_url.strip()
        if not base_url and self.databricks_host.strip():
            host = self.databricks_host.strip().rstrip("/")
            if "://" not in host:
                host = f"https://{host}"
            base_url = f"{host}/ai-gateway/mlflow/v1"
        base_url = base_url.rstrip("/")
        parsed_base_url = urlsplit(base_url)
        if parsed_base_url.scheme != "https" or not parsed_base_url.netloc:
            raise ValueError(
                "Databricks Unity Gateway models require FLEET_LLM_BASE_URL or DATABRICKS_HOST "
                "with an HTTPS workspace URL"
            )
        return base_url

    def _gateway_api_key(self) -> str:
        api_key = self.databricks_token.get_secret_value()
        if not api_key:
            raise ValueError("Databricks Unity Gateway models require DATABRICKS_TOKEN")
        return api_key


@lru_cache(maxsize=1)
def get_settings(mlflow_mode: Literal["local", "databricks"] = "local") -> FleetSettings:
    """Load and cache process settings."""
    return FleetSettings(mlflow_mode=mlflow_mode)
