"""Typed settings and model construction for Fleet-RLM Lite."""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import tomllib
from functools import lru_cache
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

from fleet_rlm.models import Endpoint, EnvReference, ModelRoute, ResolvedModel, Role
from fleet_rlm.workspace import MAX_WORKSPACE_FILE_BYTES

_SECRET_FIELDS = {
    "fleet_database_url",
    "fleet_database_migration_url",
    "fleet_daytona_api_key",
    "databricks_token",
    "fleet_llm_api_key",
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
        "job_max_tokens": "job_max_tokens",
        "linked_children": "linked_children",
    },
    "gateway": {"host": "databricks_host"},
    "llm": {
        "model": "fleet_llm_model",
        "api_base": "fleet_llm_api_base",
        "api_key_env": "fleet_llm_api_key_env",
        "adapter": "fleet_llm_adapter",
        "response_format": "fleet_llm_response_format",
    },
    "decision": {"model": "typesafe_model"},
    "daytona": {
        "org_id": "fleet_daytona_org_id",
        "volume_id": "fleet_daytona_volume_id",
        "auto_stop_minutes": "daytona_auto_stop_minutes",
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
    "mlflow": {"mode": "mlflow_mode"},
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
_TOML_FIELDS["llm.judge"] = {
    "model": "fleet_correctness_model",
    **{name: f"fleet_judge_{name}" for name in ("max_tokens", "temperature", "num_retries", "cache")},
}
for _role in ("root", "sub", "reflection", "judge"):
    _TOML_FIELDS[f"llm.{_role}"].update(
        {name: f"fleet_{_role}_{name}" for name in ("api_base", "api_key_env", "adapter", "response_format")}
    )
_TOML_FIELDS["llm"].update(
    {name: f"fleet_llm_{name}" for name in ("max_tokens", "temperature", "num_retries", "cache")}
)
RETIRED_MLFLOW_ENV = {
    "FLEET_MLFLOW_MODE",
    "MLFLOW_TRACKING_URI",
    "MLFLOW_EXPERIMENT_NAME",
    *(f"FLEET_MLFLOW_{key.upper()}" for key in _TOML_FIELDS["mlflow.databricks"]),
}
# The shipped Fleet deadline: none. A Turn runs to its model-call budget unless a Session
# configures a finite deadline, so a legitimate long Turn is never cut off by a default.
DEFAULT_TURN_TIMEOUT_SECONDS = 0
_TURN_TIMEOUT_RULE = "0 or between 10 and 3600 seconds"


class DeadlinePolicy(NamedTuple):
    """The one deadline decision for a Turn: where it came from and how long it is."""

    source: str
    effective_seconds: int | None


def validate_turn_timeout_seconds(
    value: Any,
    *,
    field: str = "turn_timeout_seconds",
    allow_numeric_text: bool = False,
) -> int:
    """Accept exactly `0` or `10..3600` as an integer; reject booleans, floats, and other text.

    Environment transports are inherently textual, so `allow_numeric_text` lets the
    settings sources that are strings convert a bare integer. Structured surfaces
    (init keywords, TOML, HTTP JSON, TUI arguments) stay strict.

    Raises:
        ValueError: The value has an invalid type or is outside the allowed range;
            `field` identifies the setting in the error message.
    """
    if isinstance(value, str) and allow_numeric_text and re.fullmatch(r"-?[0-9]+", value):
        value = int(value)
    if type(value) is not int:
        raise ValueError(f"{field} must be an integer: the value must be exactly {_TURN_TIMEOUT_RULE}")
    if value != 0 and not 10 <= value <= 3600:
        raise ValueError(f"{field} must be exactly {_TURN_TIMEOUT_RULE}")
    return value


def resolve_turn_deadline(
    request_seconds: int | None,
    configured_seconds: int,
    *,
    configured_source: str = "default",
) -> DeadlinePolicy:
    """Resolve one Turn's deadline policy: request, then configuration, then the default.

    `None` from the request means inherit; `0` means no Fleet deadline. The server
    reports only sources it can observe, so a client `/timeout` override arrives as
    `request` and never as a command source.

    Inputs must already be validated durations in seconds. The result carries the
    source and effective seconds, using None for no deadline. When `configured_source`
    is `default`, the shipped duration is used regardless of `configured_seconds`.
    """
    if request_seconds is not None:
        return DeadlinePolicy("request", request_seconds if request_seconds > 0 else None)
    if configured_source != "default":
        return DeadlinePolicy("configuration", configured_seconds if configured_seconds > 0 else None)
    return DeadlinePolicy("default", DEFAULT_TURN_TIMEOUT_SECONDS)


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
    if schema != {"schema_version": 5} or type(schema.get("schema_version")) is not int:
        raise ValueError(
            "Fleet TOML requires [config] schema_version = 5; see DSPY_MIGRATION.md for direct model configuration"
        )
    if "models" in data or "connections" in data:
        raise ValueError("[models] and [connections] were removed; configure [llm] directly")
    tables = dict(data)
    for parent in ("llm", "mlflow"):
        if parent not in tables:
            continue
        children = tables.pop(parent)
        if not isinstance(children, dict):
            raise ValueError(f"Fleet TOML {parent} must be a table")
        if parent == "mlflow" and "mode" in children:
            tables["mlflow"] = {"mode": children.pop("mode")}
        if parent == "llm":
            defaults = {name: children.pop(name) for name in list(children) if not isinstance(children[name], dict)}
            if defaults:
                tables["llm"] = defaults
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
        extra="forbid",
        validate_by_name=True,
        hide_input_in_errors=True,
    )

    fleet_llm_model: ModelRoute = Field(default="openai/gpt-4o-mini", validation_alias="FLEET_LLM_MODEL")
    fleet_llm_api_base: Endpoint = Field(default="", validation_alias="FLEET_LLM_BASE_URL")
    fleet_llm_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="FLEET_LLM_API_KEY")
    fleet_llm_adapter: Literal["chat", "json"] = "chat"
    fleet_llm_response_format: Literal["auto", "json_object"] = "auto"
    fleet_llm_api_key_env: EnvReference = ""
    fleet_correctness_model: ModelRoute | Literal[""] = ""
    fleet_judge_api_base: Endpoint | None = None
    fleet_judge_api_key_env: EnvReference | None = None
    fleet_judge_adapter: Literal["chat", "json"] | None = None
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
            self._resolve_models()
        finally:
            _SETTINGS_SOURCES.reset(token)

    def _resolve_models(self) -> None:
        from dotenv import dotenv_values

        def credential(reference: str, default: SecretStr) -> SecretStr:
            if not reference:
                return default
            if reference in self._resolved_credentials:
                return self._resolved_credentials[reference]
            value = os.environ.get(reference)
            if value is None and self._credential_env_file:
                value = dotenv_values(self._credential_env_file).get(reference)
            if not value:
                raise ValueError(f"Missing credential environment variable: {reference}")
            self._resolved_credentials[reference] = SecretStr(value)
            return self._resolved_credentials[reference]

        default_key = credential(self.fleet_llm_api_key_env, self.fleet_llm_api_key)
        self._resolved_lms["shared"] = ResolvedModel(
            model=self.fleet_llm_model,
            api_base=self.fleet_llm_api_base,
            api_key=default_key,
            adapter=self.fleet_llm_adapter,
            response_format=self.fleet_llm_response_format,
        )
        for role in ("root", "sub", "reflection", "judge"):
            selected = self.fleet_correctness_model if role == "judge" else getattr(self, f"fleet_{role}_model")
            independent = bool(selected)
            base = getattr(self, f"fleet_{role}_api_base")
            reference = getattr(self, f"fleet_{role}_api_key_env")
            adapter = getattr(self, f"fleet_{role}_adapter")
            options = {}
            for name in ("max_tokens", "temperature", "num_retries", "cache"):
                field = f"fleet_{role}_{name}"
                value = getattr(self, field)
                shared = getattr(self, f"fleet_llm_{name}")
                if self.configuration_source(field) == "default" and shared is not None:
                    value = shared
                options[name] = value
            self._resolved_lms[role] = ResolvedModel(
                model=selected
                or (
                    "openai/system.ai.deepseek-v4-1-flash-1017"
                    if role in {"root", "sub"} and self.configuration_source("fleet_llm_model") == "default"
                    else self.fleet_llm_model
                ),
                api_base=base if base is not None else ("" if independent else self.fleet_llm_api_base),
                api_key=credential(reference or "", SecretStr("") if independent else default_key),
                adapter=adapter or self.fleet_llm_adapter,
                response_format=getattr(self, f"fleet_{role}_response_format") or self.fleet_llm_response_format,
                **options,
            )

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
    fleet_root_adapter: Literal["chat", "json"] | None = None
    fleet_root_response_format: Literal["auto", "json_object"] | None = None

    fleet_sub_model: ModelRoute | Literal[""] = ""
    fleet_sub_api_base: Endpoint | None = None
    fleet_sub_api_key_env: EnvReference | None = None
    fleet_sub_adapter: Literal["chat", "json"] | None = None
    fleet_sub_response_format: Literal["auto", "json_object"] | None = None

    fleet_reflection_model: ModelRoute | Literal[""] = ""
    fleet_reflection_api_base: Endpoint | None = None
    fleet_reflection_api_key_env: EnvReference | None = None
    fleet_reflection_adapter: Literal["chat", "json"] | None = None
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

    @field_validator(
        "fleet_database_url",
    )
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
        """MLflow diagnostic providers use their own native environment configuration."""
        return {}

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
        """Build the independently configured DSPy correctness judge."""
        from fleet_rlm.models import build_lm

        return build_lm(self, self.fleet_correctness_model, "judge")

    def list_models(self, scope: str = "", role: Role | Literal["shared"] = "root") -> list[str]:
        """Browse the selected role's explicit compatible endpoint."""
        from fleet_rlm.models import list_models

        return list_models(self, scope=scope, role=role)


@lru_cache(maxsize=1)
def get_settings(mlflow_mode: Literal["local", "databricks"] | None = None) -> FleetSettings:
    """Load and cache process settings."""
    return FleetSettings(**({"mlflow_mode": mlflow_mode} if mlflow_mode is not None else {}))
