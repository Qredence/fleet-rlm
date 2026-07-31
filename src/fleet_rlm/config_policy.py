"""Editable, non-secret Fleet TOML policy service.

This module owns the committed policy document only.  It never reads values from
``.env`` or process environment variables, so callers cannot use it to recover
credentials or runtime overrides.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import tomlkit
from tomlkit import TOMLDocument

from fleet_rlm.config import (
    FleetConfigurationError,
    Settings,
    _deep_merge,
    _flatten_policy,
    _require_mapping,
    _validate_policy_table,
)

EditorKind = Literal["text", "number", "boolean", "single_choice", "multi_choice"]


class PolicyConflictError(ValueError):
    """Raised when a caller attempts to overwrite a newer policy revision."""


class PolicyAccessError(ValueError):
    """Raised when a policy document is unsafe to edit."""


@dataclass(frozen=True, slots=True)
class PolicyField:
    path: str
    group: str
    label: str
    editor: EditorKind
    choices: tuple[str, ...] = ()
    settings_field: str | None = None


_FIELDS: tuple[PolicyField, ...] = (
    PolicyField("application.name", "Application", "Name", "text", settings_field="app_name"),
    PolicyField(
        "runtime.environment", "Runtime", "Environment", "single_choice", ("deno", "daytona"), "run_environment"
    ),
    PolicyField(
        "runtime.turn_timeout_seconds",
        "Runtime",
        "Turn timeout seconds",
        "number",
        settings_field="turn_timeout_seconds",
    ),
    PolicyField(
        "runtime.max_active_daytona_leases",
        "Runtime",
        "Maximum Daytona leases",
        "number",
        settings_field="max_active_daytona_leases",
    ),
    PolicyField(
        "runtime.heartbeat_seconds", "Runtime", "Heartbeat seconds", "number", settings_field="run_heartbeat_seconds"
    ),
    PolicyField(
        "runtime.stale_after_seconds",
        "Runtime",
        "Stale after seconds",
        "number",
        settings_field="run_stale_after_seconds",
    ),
    PolicyField("llm.root.model", "Root LLM", "Model", "text", settings_field="root_model"),
    PolicyField(
        "llm.root.api_key_env",
        "Root LLM",
        "API key environment variable",
        "text",
        settings_field="root_llm_api_key_env",
    ),
    PolicyField("llm.root.base_url", "Root LLM", "Base URL", "text", settings_field="root_llm_base_url"),
    PolicyField("llm.root.base_url_env", "Root LLM", "Base URL environment variable", "text"),
    PolicyField("llm.root.max_tokens", "Root LLM", "Maximum tokens", "number", settings_field="root_llm_max_tokens"),
    PolicyField("llm.root.temperature", "Root LLM", "Temperature", "number", settings_field="root_llm_temperature"),
    PolicyField("llm.root.cache", "Root LLM", "Cache", "boolean", settings_field="root_llm_cache"),
    PolicyField("llm.root.num_retries", "Root LLM", "Retries", "number", settings_field="root_llm_num_retries"),
    PolicyField("llm.sub.model", "Sub LLM", "Model", "text", settings_field="sub_model"),
    PolicyField(
        "llm.sub.api_key_env", "Sub LLM", "API key environment variable", "text", settings_field="sub_llm_api_key_env"
    ),
    PolicyField("llm.sub.base_url", "Sub LLM", "Base URL", "text", settings_field="sub_llm_base_url"),
    PolicyField("llm.sub.base_url_env", "Sub LLM", "Base URL environment variable", "text"),
    PolicyField("llm.sub.max_tokens", "Sub LLM", "Maximum tokens", "number", settings_field="sub_llm_max_tokens"),
    PolicyField("llm.sub.temperature", "Sub LLM", "Temperature", "number", settings_field="sub_llm_temperature"),
    PolicyField("llm.sub.cache", "Sub LLM", "Cache", "boolean", settings_field="sub_llm_cache"),
    PolicyField("llm.sub.num_retries", "Sub LLM", "Retries", "number", settings_field="sub_llm_num_retries"),
    PolicyField("rlm.max_iterations", "RLM", "Maximum iterations", "number", settings_field="rlm_max_iterations"),
    PolicyField("rlm.max_llm_calls", "RLM", "Maximum LLM calls", "number", settings_field="rlm_max_llm_calls"),
    PolicyField(
        "rlm.max_output_chars", "RLM", "Maximum output characters", "number", settings_field="rlm_max_output_chars"
    ),
    PolicyField(
        "rlm.max_execution_output_chars",
        "RLM",
        "Maximum execution output characters",
        "number",
        settings_field="rlm_max_execution_output_chars",
    ),
    PolicyField(
        "rlm.execution_timeout_s",
        "RLM",
        "Sandbox execution timeout (seconds)",
        "number",
        settings_field="rlm_execution_timeout_s",
    ),
    PolicyField(
        "rlm.recursion_max_depth", "RLM", "Recursive maximum depth", "number", settings_field="rlm_recursion_max_depth"
    ),
    PolicyField(
        "rlm.recursion_max_calls", "RLM", "Recursive maximum calls", "number", settings_field="rlm_recursion_max_calls"
    ),
    PolicyField(
        "rlm.recursion_max_prompt_chars",
        "RLM",
        "Recursive prompt character bound",
        "number",
        settings_field="rlm_recursion_max_prompt_chars",
    ),
    PolicyField(
        "rlm.recursion_child_max_iterations",
        "RLM",
        "Child maximum iterations",
        "number",
        settings_field="rlm_recursion_child_max_iterations",
    ),
    PolicyField(
        "rlm.recursion_child_max_llm_calls",
        "RLM",
        "Child maximum LLM calls",
        "number",
        settings_field="rlm_recursion_child_max_llm_calls",
    ),
    PolicyField(
        "rlm.recursion_child_max_output_chars",
        "RLM",
        "Child maximum output characters",
        "number",
        settings_field="rlm_recursion_child_max_output_chars",
    ),
    PolicyField("rlm.verbose", "RLM", "DSPy host verbose logging", "boolean", settings_field="rlm_verbose"),
    PolicyField("storage.data_root", "Storage", "Data root", "text", settings_field="data_root"),
    PolicyField(
        "storage.max_upload_bytes", "Storage", "Maximum upload bytes", "number", settings_field="max_upload_bytes"
    ),
    PolicyField(
        "storage.max_url_bytes", "Storage", "Maximum URL source bytes", "number", settings_field="max_url_bytes"
    ),
    PolicyField(
        "storage.max_artifact_bytes", "Storage", "Maximum artifact bytes", "number", settings_field="max_artifact_bytes"
    ),
    PolicyField("storage.database_url_env", "Storage", "Database URL environment variable", "text"),
    PolicyField("daytona.api_key_env", "Daytona", "API key environment variable", "text"),
    PolicyField("daytona.snapshot", "Daytona", "Snapshot", "text", settings_field="daytona_snapshot"),
    PolicyField("daytona.org_id", "Daytona", "Organization ID", "text", settings_field="daytona_org_id"),
    PolicyField("daytona.volume_name", "Daytona", "Volume name", "text", settings_field="volume_name"),
    PolicyField(
        "daytona.volume_mount_path", "Daytona", "Volume mount path", "text", settings_field="volume_mount_path"
    ),
    PolicyField(
        "logging.level",
        "Logging",
        "Level",
        "single_choice",
        ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"),
        "log_level",
    ),
    PolicyField(
        "mlflow.tracing_enabled", "MLflow", "Tracing enabled", "boolean", settings_field="mlflow_tracing_enabled"
    ),
    PolicyField(
        "mlflow.async_logging", "MLflow", "Async trace logging", "boolean", settings_field="mlflow_async_logging"
    ),
    PolicyField(
        "mlflow.trace_sampling_ratio",
        "MLflow",
        "Trace sampling ratio",
        "number",
        settings_field="mlflow_trace_sampling_ratio",
    ),
    PolicyField("mlflow.experiment_name", "MLflow", "Experiment name", "text", settings_field="mlflow_experiment_name"),
    PolicyField("mlflow.experiment_name_env", "MLflow", "Experiment environment variable", "text"),
    PolicyField("mlflow.tracking_uri", "MLflow", "Tracking URI", "text", settings_field="mlflow_tracking_uri"),
    PolicyField(
        "mlflow.expose_trace_id", "MLflow", "Expose trace ID", "boolean", settings_field="mlflow_expose_trace_id"
    ),
    PolicyField("mlflow.trace_catalog", "MLflow", "Trace catalog", "text", settings_field="mlflow_trace_catalog"),
    PolicyField("mlflow.trace_catalog_env", "MLflow", "Trace catalog environment variable", "text"),
    PolicyField("mlflow.trace_schema", "MLflow", "Trace schema", "text", settings_field="mlflow_trace_schema"),
    PolicyField("mlflow.trace_schema_env", "MLflow", "Trace schema environment variable", "text"),
    PolicyField(
        "mlflow.trace_table_prefix", "MLflow", "Trace table prefix", "text", settings_field="mlflow_trace_table_prefix"
    ),
    PolicyField("mlflow.trace_table_prefix_env", "MLflow", "Trace table prefix environment variable", "text"),
    PolicyField(
        "mlflow.tracing_sql_warehouse_id",
        "MLflow",
        "Tracing SQL warehouse ID",
        "text",
        settings_field="mlflow_tracing_sql_warehouse_id",
    ),
    PolicyField(
        "mlflow.tracing_sql_warehouse_id_env",
        "MLflow",
        "Tracing SQL warehouse environment variable",
        "text",
    ),
)
_FIELD_BY_PATH = {field.path: field for field in _FIELDS}


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    revision: str
    active_profile: str | None
    default_profile: str | None
    available_profiles: tuple[str, ...]
    scopes: tuple[dict[str, Any], ...]


class ConfigPolicyService:
    """Read and safely edit the fixed Fleet TOML policy path."""

    def __init__(self, path: Path, *, active_profile: str | None) -> None:
        self._path = path.resolve()
        self._active_profile = active_profile or None
        self._lock = threading.Lock()

    def read(self) -> PolicySnapshot:
        with self._lock:
            document, raw = self._read_document()
            return self._snapshot(document, raw)

    def update(self, *, scope: str, path: str, value: Any, revision: str) -> PolicySnapshot:
        field = _FIELD_BY_PATH.get(path)
        if field is None:
            raise FleetConfigurationError("unsupported settings field")
        normalized = self._normalize_value(field, value)
        with self._lock:
            document, raw = self._read_document()
            if revision != self._revision(raw):
                raise PolicyConflictError("settings changed; reload before saving")
            table = self._scope_table(document, scope)
            parent, key = self._parent_table(table, path)
            parent[key] = normalized
            rendered = tomlkit.dumps(document)
            self._validate(rendered)
            self._atomic_write(rendered)
            updated, updated_raw = self._read_document()
            return self._snapshot(updated, updated_raw)

    def set_default_profile(self, name: str, *, revision: str) -> PolicySnapshot:
        if not isinstance(name, str) or not name.strip():
            raise FleetConfigurationError("profile name must be a non-empty string")
        target = name.strip()
        with self._lock:
            document, raw = self._read_document()
            if revision != self._revision(raw):
                raise PolicyConflictError("settings changed; reload before saving")
            profiles = document.get("profiles")
            if not isinstance(profiles, dict) or target not in profiles:
                raise FleetConfigurationError(f"configured profile does not exist: {target}")
            config = document.get("config")
            if not isinstance(config, dict):
                config = tomlkit.table()
                document["config"] = config
            config["default_profile"] = target
            rendered = tomlkit.dumps(document)
            self._validate(rendered)
            self._atomic_write(rendered)
            updated, updated_raw = self._read_document()
            return self._snapshot(updated, updated_raw)

    def _read_document(self) -> tuple[TOMLDocument, str]:
        if self._path.is_symlink() or not self._path.is_file():
            raise PolicyAccessError("Fleet configuration file is unavailable")
        raw = self._path.read_text(encoding="utf-8")
        try:
            return tomlkit.parse(raw), raw
        except Exception as exc:  # tomlkit does not expose a stable parse error base class.
            raise FleetConfigurationError("invalid Fleet configuration TOML") from exc

    def _snapshot(self, document: TOMLDocument, raw: str) -> PolicySnapshot:
        scopes: list[dict[str, Any]] = []
        defaults = document.get("defaults")
        if isinstance(defaults, dict):
            scopes.append(self._scope("defaults", defaults))
        profiles = document.get("profiles")
        available: list[str] = []
        if isinstance(profiles, dict):
            for name, profile in profiles.items():
                if isinstance(name, str) and isinstance(profile, dict):
                    available.append(name)
                    scopes.append(self._scope(name, profile, inherited=defaults))
        config = document.get("config")
        default_profile = config.get("default_profile") if isinstance(config, dict) else None
        return PolicySnapshot(
            self._revision(raw),
            self._active_profile,
            default_profile if isinstance(default_profile, str) else None,
            tuple(available),
            tuple(scopes),
        )

    def _scope(
        self,
        name: str,
        table: dict[str, Any],
        *,
        inherited: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        values: list[dict[str, Any]] = []
        for field in _FIELDS:
            value = self._lookup(table, field.path)
            if value is _MISSING and inherited is not None:
                value = self._lookup(inherited, field.path)
            if value is _MISSING:
                continue
            values.append(
                {
                    "path": field.path,
                    "group": field.group,
                    "label": field.label,
                    "value": value,
                    "editor": field.editor,
                    "choices": list(field.choices),
                    "environment_overridden": False,
                }
            )
        return {"name": name, "fields": values}

    def _scope_table(self, document: TOMLDocument, scope: str) -> dict[str, Any]:
        if scope == "defaults":
            table = document.get("defaults")
        else:
            profiles = document.get("profiles")
            table = profiles.get(scope) if isinstance(profiles, dict) else None
        if not isinstance(table, dict):
            raise FleetConfigurationError("settings scope does not exist")
        return table

    @staticmethod
    def _parent_table(table: dict[str, Any], path: str) -> tuple[dict[str, Any], str]:
        parts = path.split(".")
        current = table
        for part in parts[:-1]:
            child = current.get(part)
            if not isinstance(child, dict):
                current[part] = tomlkit.table()
                child = current[part]
            current = child
        return current, parts[-1]

    @staticmethod
    def _lookup(table: dict[str, Any], path: str) -> Any:
        current: Any = table
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                return _MISSING
            current = current[part]
        return current.unwrap() if hasattr(current, "unwrap") else current

    @staticmethod
    def _revision(raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_value(field: PolicyField, value: Any) -> Any:
        if field.editor == "text":
            if not isinstance(value, str):
                raise FleetConfigurationError("settings value must be text")
            return value
        if field.editor == "boolean":
            if not isinstance(value, bool):
                raise FleetConfigurationError("settings value must be boolean")
            return value
        if field.editor == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise FleetConfigurationError("settings value must be numeric")
            return value
        if field.editor == "single_choice":
            if not isinstance(value, str) or value not in field.choices:
                raise FleetConfigurationError("settings value is not an allowed choice")
            return value
        if field.editor == "multi_choice":
            if not isinstance(value, list) or any(item not in field.choices for item in value):
                raise FleetConfigurationError("settings value contains an invalid choice")
            return value
        raise AssertionError(f"unsupported editor {field.editor}")

    def _validate(self, raw: str) -> None:
        """
        Validate Fleet policy TOML and its profile configurations.

        Parameters:
                raw (str): TOML content containing the Fleet policy.

        Raises:
                FleetConfigurationError: If the TOML is malformed or contains unsupported or invalid
                    configuration values.
        """
        try:
            root = _require_mapping(tomllib.loads(raw), "root")
        except tomllib.TOMLDecodeError as exc:
            raise FleetConfigurationError("invalid Fleet configuration TOML") from exc
        if set(root).difference({"config", "defaults", "profiles"}):
            raise FleetConfigurationError("unknown configuration key")
        config_table = _require_mapping(root.get("config", {}), "config")
        if set(config_table).difference({"schema_version", "default_profile"}):
            raise FleetConfigurationError("unknown configuration key")
        if config_table.get("schema_version") != 1:
            raise FleetConfigurationError("config.schema_version must be 1")
        default_profile = config_table.get("default_profile")
        if default_profile is not None and not isinstance(default_profile, str):
            raise FleetConfigurationError("config.default_profile must be a string")
        defaults = _require_mapping(root.get("defaults", {}), "defaults")
        profiles = _require_mapping(root.get("profiles", {}), "profiles")
        _validate_policy_table(defaults, "defaults", allow_partial_llm=True)
        for profile, value in profiles.items():
            selected = _require_mapping(value, f"profiles.{profile}")
            _validate_policy_table(selected, f"profiles.{profile}")
            values = _flatten_policy(_deep_merge(defaults, selected))
            try:
                Settings.model_validate(values)
            except ValueError as exc:
                raise FleetConfigurationError("invalid Fleet configuration policy") from exc

    def _atomic_write(self, content: str) -> None:
        parent = self._path.parent
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self._path.name}.", suffix=".tmp", dir=parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, self._path.stat().st_mode)
            os.replace(temporary, self._path)
            directory = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)


_MISSING = object()
