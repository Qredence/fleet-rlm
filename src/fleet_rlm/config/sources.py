"""TOML source parsing, retired environment checks, and deadline policy helpers."""

from __future__ import annotations

import contextvars
import os
import re
import tomllib
from pathlib import Path
from typing import Any, NamedTuple

from pydantic import TypeAdapter, ValidationError
from pydantic_settings import BaseSettings

# Dev and holdout each need this many labelled examples before a run can tell a gain from noise.
MIN_SPLIT_EXAMPLES = 5
_SECRET_FIELDS = {
    "fleet_database_url",
    "fleet_database_migration_url",
    "fleet_daytona_api_key",
    "databricks_token",
    "fleet_llm_api_key",
    "typesafe_api_key",
}
_TOML_FIELDS: dict[str, dict[str, str]] = {
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
        "model_type": "fleet_llm_model_type",
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
        {
            name: f"fleet_{_role}_{name}"
            for name in ("api_base", "api_key_env", "adapter", "model_type", "response_format")
        }
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
            "Fleet TOML requires [config] schema_version = 5; see docs/reference/configuration.md for schema details"
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
    result: dict[str, Any] = {}
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
