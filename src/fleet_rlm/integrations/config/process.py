"""Typed process configuration with YAML and environment precedence.

This module is intentionally resource-free: importing it only defines models
and loaders. Runtime clients are constructed by their owning integration.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LmRoleConfig(StrictConfigModel):
    model: str | None = None
    temperature: float = 0.0
    max_tokens: int = Field(default=2048, gt=0)
    request_timeout_s: float = Field(default=30, gt=0)


class LlmRolesConfig(StrictConfigModel):
    planner: LmRoleConfig = Field(default_factory=LmRoleConfig)
    delegate: LmRoleConfig = Field(default_factory=lambda: LmRoleConfig(max_tokens=4096, request_timeout_s=120))
    delegate_small: LmRoleConfig = Field(default_factory=lambda: LmRoleConfig(request_timeout_s=60))
    judge: LmRoleConfig = Field(default_factory=lambda: LmRoleConfig(max_tokens=1024, request_timeout_s=60))


class LlmProcessConfig(StrictConfigModel):
    roles: LlmRolesConfig = Field(default_factory=LlmRolesConfig)


class RecursionConfig(StrictConfigModel):
    max_depth: int = Field(default=2, ge=0)
    delegate_max_calls_per_turn: int = Field(default=8, ge=0)
    child_isolation_mode: Literal["auto", "context"] = "auto"
    child_fork_fallback: Literal["clean", "fail"] = "clean"


class RlmProcessConfig(StrictConfigModel):
    max_iters: int = Field(default=20, gt=0)
    max_llm_calls: int = Field(default=50, gt=0)
    max_output_chars: int = Field(default=10000, gt=0)
    verbose: bool = False
    recursion: RecursionConfig = Field(default_factory=RecursionConfig)


class DaytonaPoolConfig(StrictConfigModel):
    max_concurrent_sandboxes: int = Field(default=5, ge=1, le=50)


class DaytonaLifecycleConfig(StrictConfigModel):
    session_lifecycle: Literal["pause", "delete"] = "delete"


class DaytonaProcessConfig(StrictConfigModel):
    api_url: str | None = None
    target: str | None = None
    volume_name: str | None = None
    execution_timeout_s: int = Field(default=900, gt=0)
    secret_name: str = "LITELLM"
    pool: DaytonaPoolConfig = Field(default_factory=DaytonaPoolConfig)
    lifecycle: DaytonaLifecycleConfig = Field(default_factory=DaytonaLifecycleConfig)


class PersistenceProcessConfig(StrictConfigModel):
    database_url: str | None = None
    database_required: bool = False


class MlflowProcessConfig(StrictConfigModel):
    enabled: bool = False
    tracking_uri: str | None = None
    experiment_name: str | None = None
    auto_start: bool = True


class ObservabilityProcessConfig(StrictConfigModel):
    mlflow: MlflowProcessConfig = Field(default_factory=MlflowProcessConfig)


class ApiProcessConfig(StrictConfigModel):
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    auth_mode: Literal["dev", "entra", "neon"] = "dev"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])


class ProcessConfig(StrictConfigModel):
    llm: LlmProcessConfig = Field(default_factory=LlmProcessConfig)
    rlm: RlmProcessConfig = Field(default_factory=RlmProcessConfig)
    daytona: DaytonaProcessConfig = Field(default_factory=DaytonaProcessConfig)
    persistence: PersistenceProcessConfig = Field(default_factory=PersistenceProcessConfig)
    observability: ObservabilityProcessConfig = Field(default_factory=ObservabilityProcessConfig)
    api: ApiProcessConfig = Field(default_factory=ApiProcessConfig)


class ConfigResolution(StrictConfigModel):
    config: ProcessConfig
    sources: dict[str, Literal["default", "yaml", "environment", "override"]]
    config_path: Path | None = None

    def diagnostics(self) -> dict[str, dict[str, object]]:
        """Return safe major-value diagnostics without credential material."""
        values = self.config.model_dump()
        paths = (
            "llm.roles.planner.model",
            "rlm.max_iters",
            "rlm.max_llm_calls",
            "daytona.target",
            "daytona.pool.max_concurrent_sandboxes",
            "persistence.database_required",
            "observability.mlflow.enabled",
            "api.auth_mode",
            "api.port",
        )
        diagnostics = {
            path: {"value": _nested_get(values, path), "source": self.sources.get(path, "default")} for path in paths
        }
        tracking_path = "observability.mlflow.tracking_uri"
        diagnostics[tracking_path] = {
            "value": _safe_uri(_nested_get(values, tracking_path)),
            "source": self.sources.get(tracking_path, "default"),
        }
        return diagnostics


_ENV_ALIASES: dict[str, str] = {
    "DSPY_LM_MODEL": "llm.roles.planner.model",
    "DSPY_DELEGATE_LM_MODEL": "llm.roles.delegate.model",
    "DSPY_DELEGATE_LM_SMALL_MODEL": "llm.roles.delegate_small.model",
    "DSPY_LM_MAX_TOKENS": "llm.roles.planner.max_tokens",
    "DSPY_DELEGATE_LM_MAX_TOKENS": "llm.roles.delegate.max_tokens",
    "DSPY_PLANNER_LM_TEMPERATURE": "llm.roles.planner.temperature",
    "DSPY_PLANNER_LM_TIMEOUT_S": "llm.roles.planner.request_timeout_s",
    "DSPY_DELEGATE_LM_TIMEOUT_S": "llm.roles.delegate.request_timeout_s",
    "RLM_MAX_ITERATIONS": "rlm.max_iters",
    "RLM_MAX_LLM_CALLS": "rlm.max_llm_calls",
    "RLM_MAX_DEPTH": "rlm.recursion.max_depth",
    "DELEGATE_MAX_CALLS_PER_TURN": "rlm.recursion.delegate_max_calls_per_turn",
    "AGENT_MAX_OUTPUT_CHARS": "rlm.max_output_chars",
    "RLM_CHILD_ISOLATION_MODE": "rlm.recursion.child_isolation_mode",
    "RLM_CHILD_FORK_FALLBACK": "rlm.recursion.child_fork_fallback",
    "DAYTONA_API_URL": "daytona.api_url",
    "DAYTONA_TARGET": "daytona.target",
    "VOLUME_NAME": "daytona.volume_name",
    "TIMEOUT": "daytona.execution_timeout_s",
    "SECRET_NAME": "daytona.secret_name",
    "FLEET_MAX_CONCURRENT_SANDBOXES": "daytona.pool.max_concurrent_sandboxes",
    "FLEET_SESSION_LIFECYCLE": "daytona.lifecycle.session_lifecycle",
    "DATABASE_URL": "persistence.database_url",
    "DATABASE_REQUIRED": "persistence.database_required",
    "MLFLOW_ENABLED": "observability.mlflow.enabled",
    "MLFLOW_TRACKING_URI": "observability.mlflow.tracking_uri",
    "MLFLOW_EXPERIMENT": "observability.mlflow.experiment_name",
    "MLFLOW_EXPERIMENT_NAME": "observability.mlflow.experiment_name",
    "MLFLOW_AUTO_START": "observability.mlflow.auto_start",
    "AUTH_MODE": "api.auth_mode",
    "PORT": "api.port",
    "CORS_ALLOWED_ORIGINS": "api.cors_origins",
}

_LEGACY_OVERRIDE_PATHS: dict[str, str] = {
    "agent.model": "llm.roles.planner.model",
    "llm.model": "llm.roles.planner.model",
    "llm.delegate_model": "llm.roles.delegate.model",
    "llm.delegate_small_model": "llm.roles.delegate_small.model",
    "llm.max_tokens": "llm.roles.planner.max_tokens",
    "llm.delegate_max_tokens": "llm.roles.delegate.max_tokens",
    "llm.temperature": "llm.roles.planner.temperature",
    "sandbox.timeout": "daytona.execution_timeout_s",
    "sandbox.secret_name": "daytona.secret_name",
    "sandbox.daytona_api_url": "daytona.api_url",
    "sandbox.daytona_target": "daytona.target",
    "volumes.name": "daytona.volume_name",
    "rlm_settings.max_depth": "rlm.recursion.max_depth",
    "rlm_settings.max_iters": "rlm.max_iters",
    "rlm_settings.max_iterations": "rlm.max_iters",
    "rlm_settings.max_llm_calls": "rlm.max_llm_calls",
    "rlm_settings.max_output_chars": "rlm.max_output_chars",
    "rlm_settings.delegate_max_calls_per_turn": "rlm.recursion.delegate_max_calls_per_turn",
    "rlm_settings.child_isolation_mode": "rlm.recursion.child_isolation_mode",
    "rlm_settings.child_fork_fallback": "rlm.recursion.child_fork_fallback",
    "rlm_settings.verbose": "rlm.verbose",
}


def _nested_set(data: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = data
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _nested_get(data: dict[str, Any], path: str) -> Any:
    node: Any = data
    for part in path.split("."):
        node = node[part]
    return node


def _yaml_leaf_paths(value: object, prefix: str = "") -> set[str]:
    if not isinstance(value, dict):
        return {prefix} if prefix else set()
    paths: set[str] = set()
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        paths.update(_yaml_leaf_paths(child, path))
    return paths


def _reject_secret_yaml_fields(value: object, prefix: str = "") -> None:
    if not isinstance(value, dict):
        return
    secret_names = {
        "anthropic_api_key",
        "api_key",
        "database_url",
        "daytona_api_key",
        "dev_jwt_secret",
        "encryption_key",
        "mlflow_tracking_password",
        "mlflow_tracking_token",
        "openai_api_key",
        "openrouter_api_key",
        "password",
        "posthog_api_key",
        "private_key",
        "token",
    }
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        normalized = str(key).strip().lower()
        if normalized in secret_names or normalized.endswith("_api_key"):
            raise ValueError(f"Secret configuration field is not allowed in YAML: {path}")
        _reject_secret_yaml_fields(child, path)


def _safe_uri(value: object) -> str | None:
    """Remove credentials, query parameters, and fragments from a diagnostic URI."""
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    if not parsed.scheme or not hostname:
        return "configured"
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = f"{hostname}:{parsed.port}" if parsed.port is not None else hostname
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _uri_contains_credentials(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlsplit(value)
    if parsed.username is not None or parsed.password is not None:
        return True
    sensitive_markers = ("key", "password", "secret", "token")
    return any(any(marker in key.lower() for marker in sensitive_markers) for key, _ in parse_qsl(parsed.query))


def packaged_config_path() -> Path:
    """Return the canonical packaged process configuration path."""
    return Path(__file__).with_name("config.yaml")


def _apply_overrides(
    raw: dict[str, Any],
    overrides: Sequence[str],
    sources: dict[str, Literal["default", "yaml", "environment", "override"]],
) -> None:
    valid_paths = _yaml_leaf_paths(ProcessConfig().model_dump())
    for token in overrides:
        path, separator, raw_value = token.partition("=")
        if not separator or not path.strip():
            raise ValueError("Configuration overrides must use dotted.path=value syntax")
        canonical_path = _LEGACY_OVERRIDE_PATHS.get(path.strip(), path.strip())
        if canonical_path not in valid_paths:
            raise ValueError(f"Unknown configuration override path: {path.strip()}")
        try:
            value = yaml.safe_load(raw_value)
        except yaml.YAMLError:
            raise ValueError(f"Invalid configuration override value at: {path.strip()}") from None
        _nested_set(raw, canonical_path, value)
        sources[canonical_path] = "override"


def load_process_config(
    config_path: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    overrides: Sequence[str] = (),
) -> ConfigResolution:
    """Load typed defaults, then YAML, then compatible environment aliases."""
    env = os.environ if environ is None else environ
    resolved_path = config_path
    if resolved_path is None:
        resolved_path = (
            Path(env["FLEET_RLM_CONFIG_PATH"]) if env.get("FLEET_RLM_CONFIG_PATH") else packaged_config_path()
        )

    raw: dict[str, Any] = {}
    sources: dict[str, Literal["default", "yaml", "environment", "override"]] = {}
    if resolved_path is not None and resolved_path.is_file():
        try:
            loaded = yaml.safe_load(resolved_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            raise ValueError("Invalid configuration YAML syntax") from None
        if not isinstance(loaded, dict):
            raise ValueError("Process config YAML must contain a mapping at the document root")
        raw = deepcopy(loaded)
        _reject_secret_yaml_fields(raw)
        yaml_paths = _yaml_leaf_paths(raw)
        tracking_uri = (
            _nested_get(raw, "observability.mlflow.tracking_uri")
            if "observability.mlflow.tracking_uri" in yaml_paths
            else None
        )
        if _uri_contains_credentials(tracking_uri):
            raise ValueError("observability.mlflow.tracking_uri contains credentials and must be sanitized")
        sources.update(dict.fromkeys(yaml_paths, "yaml"))

    for env_name, path in _ENV_ALIASES.items():
        value = env.get(env_name)
        if value is None or not value.strip():
            continue
        if path == "api.cors_origins":
            value = [item.strip() for item in value.split(",") if item.strip()]
        _nested_set(raw, path, value)
        sources[path] = "environment"

    _apply_overrides(raw, overrides, sources)

    try:
        config = ProcessConfig.model_validate(raw)
    except ValidationError as exc:
        issues = []
        for error in exc.errors(include_input=False, include_context=False, include_url=False):
            location = ".".join(str(part) for part in error["loc"]) or "root"
            issues.append(f"{location} ({error['type']})")
        raise ValueError(f"Invalid configuration: {', '.join(issues)}") from None
    return ConfigResolution(config=config, sources=sources, config_path=resolved_path)


def server_config_values(config: ProcessConfig, *, include_paths: set[str] | None = None) -> dict[str, object]:
    """Project canonical process settings onto the existing server model."""
    planner = config.llm.roles.planner
    delegate = config.llm.roles.delegate
    projected = {
        "llm.roles.planner.model": ("agent_model", planner.model),
        "llm.roles.delegate.model": ("agent_delegate_model", delegate.model),
        "llm.roles.delegate_small.model": ("agent_delegate_small_model", config.llm.roles.delegate_small.model),
        "llm.roles.planner.max_tokens": ("planner_max_tokens", planner.max_tokens),
        "llm.roles.delegate.max_tokens": ("agent_delegate_max_tokens", delegate.max_tokens),
        "llm.roles.planner.request_timeout_s": ("planner_lm_timeout_s", planner.request_timeout_s),
        "llm.roles.planner.temperature": ("planner_temperature", planner.temperature),
        "llm.roles.delegate.request_timeout_s": ("delegate_lm_timeout_s", delegate.request_timeout_s),
        "rlm.max_iters": ("rlm_max_iterations", config.rlm.max_iters),
        "rlm.max_llm_calls": ("rlm_max_llm_calls", config.rlm.max_llm_calls),
        "rlm.max_output_chars": ("agent_max_output_chars", config.rlm.max_output_chars),
        "rlm.recursion.max_depth": ("rlm_max_depth", config.rlm.recursion.max_depth),
        "rlm.recursion.delegate_max_calls_per_turn": (
            "delegate_max_calls_per_turn",
            config.rlm.recursion.delegate_max_calls_per_turn,
        ),
        "rlm.recursion.child_isolation_mode": (
            "rlm_child_isolation_mode",
            config.rlm.recursion.child_isolation_mode,
        ),
        "rlm.recursion.child_fork_fallback": (
            "rlm_child_fork_fallback",
            config.rlm.recursion.child_fork_fallback,
        ),
        "persistence.database_url": ("database_url", config.persistence.database_url),
        "persistence.database_required": ("database_required", config.persistence.database_required),
        "api.auth_mode": ("auth_mode", config.api.auth_mode),
        "api.cors_origins": ("cors_allowed_origins", config.api.cors_origins),
        "daytona.execution_timeout_s": ("timeout", config.daytona.execution_timeout_s),
        "daytona.secret_name": ("secret_name", config.daytona.secret_name),
        "daytona.volume_name": ("volume_name", config.daytona.volume_name),
    }
    selected = (
        projected.items()
        if include_paths is None
        else ((path, item) for path, item in projected.items() if path in include_paths)
    )
    return {field_name: value for _, (field_name, value) in selected}


__all__ = [
    "ConfigResolution",
    "ProcessConfig",
    "load_process_config",
    "packaged_config_path",
    "server_config_values",
]
