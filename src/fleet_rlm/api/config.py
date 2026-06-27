"""Server runtime configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import (
    Field,
    ValidationInfo,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from fleet_rlm.integrations.config.runtime_settings import resolve_env_path

if TYPE_CHECKING:
    from fleet_rlm.integrations.config.env import AppConfig


DEFAULT_SERVER_VOLUME_NAME = "rlm-volume-dspy"


def _resolve_server_env_path() -> Path:
    """Resolve a stable .env path for server runtime settings."""
    return resolve_env_path(
        start_paths=[
            Path(__file__).resolve().parent,
            Path.cwd(),
        ]
    )


def resolve_server_volume_name(config: AppConfig) -> str | None:
    """Resolve the server-side volume name from shared app config."""
    volume_name = config.volumes.name
    return volume_name if volume_name is not None else DEFAULT_SERVER_VOLUME_NAME


def _looks_like_managed_runtime(
    *,
    port: str | None = None,
    cwd: Path | None = None,
) -> bool:
    """Return whether the current process appears to run on a managed host.

    FastAPI Cloud runs the app from ``/app`` and injects ``PORT``. Detecting
    that combination lets us fail fast if the deploy accidentally boots with
    local defaults instead of the required cloud configuration.
    """

    resolved_port = (port if port is not None else os.getenv("PORT") or "").strip()
    resolved_cwd = cwd if cwd is not None else Path.cwd()
    return bool(resolved_port) and resolved_cwd == Path("/app")


class ServerRuntimeConfig(BaseSettings):
    """Server runtime configuration loaded from environment variables.

    Fields are automatically populated from environment variables matching
    the field name (case-insensitive).  For example, ``app_env`` reads from
    ``APP_ENV``, ``volume_name`` reads from ``VOLUME_NAME``, etc.
    """

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    env_path: Path = Field(default_factory=_resolve_server_env_path)
    app_env: Literal["local", "staging", "production"] = "local"
    secret_name: str = "LITELLM"
    volume_name: str | None = None
    timeout: int = 900
    react_max_iters: int = 15
    deep_react_max_iters: int = 35
    enable_adaptive_iters: bool = True
    rlm_max_iterations: int = 30
    rlm_max_llm_calls: int = 50
    rlm_action_max_tokens: int = Field(default=2048, alias="FLEET_RLM_ACTION_MAX_TOKENS")
    rlm_max_depth: int = 2
    rlm_child_isolation_mode: Literal["auto", "context"] = Field(default="auto", alias="RLM_CHILD_ISOLATION_MODE")
    rlm_child_fork_fallback: Literal["clean", "fail"] = Field(default="clean", alias="RLM_CHILD_FORK_FALLBACK")
    delegate_max_calls_per_turn: int = 8
    delegate_result_truncation_chars: int = 8000
    delegate_execution_timeout: int = Field(default=300, alias="RLM_DELEGATE_EXECUTION_TIMEOUT")
    delegate_max_iterations: int = Field(default=8, alias="RLM_DELEGATE_MAX_ITERATIONS")
    delegate_adapter: str = Field(default="json", alias="RLM_DELEGATE_ADAPTER")
    daytona_broker_health_timeout: float = Field(default=20.0, alias="DAYTONA_BROKER_HEALTH_TIMEOUT")
    daytona_broker_tool_call_timeout: float = Field(default=180.0, alias="DAYTONA_BROKER_TOOL_CALL_TIMEOUT")
    daytona_broker_start_retries: int = Field(default=1, alias="DAYTONA_BROKER_START_RETRIES")
    interpreter_async_execute: bool = True
    # Interpreter pool settings
    interpreter_pool_size: int = Field(default=2, alias="INTERPRETER_POOL_SIZE")
    interpreter_pool_overflow_max: int = Field(default=4, alias="INTERPRETER_POOL_OVERFLOW_MAX")
    interpreter_pool_acquire_timeout: float = Field(default=30.0, alias="INTERPRETER_POOL_ACQUIRE_TIMEOUT")
    interpreter_pool_health_interval: float = Field(default=30.0, alias="INTERPRETER_POOL_HEALTH_INTERVAL")
    # Daytona runner routing (0.177+): comma-separated tags to target specific runners
    daytona_runner_tags: list[str] | None = Field(default=None, alias="DAYTONA_RUNNER_TAGS")
    # Auto pool sizing (0.177+): compute pool_size from runner CPU capacity
    interpreter_pool_auto_size: bool = Field(default=False, alias="INTERPRETER_POOL_AUTO_SIZE")
    interpreter_pool_cpu_per_sandbox: int = Field(default=2, alias="INTERPRETER_POOL_CPU_PER_SANDBOX")
    agent_guardrail_mode: Literal["off", "warn", "strict"] = "off"
    agent_min_substantive_chars: int = 20
    agent_max_output_chars: int = 5000
    ws_default_workspace_id: str = "default"
    ws_default_user_id: str = "anonymous"
    ws_enforce_react_interlocutor: bool = True
    ws_default_execution_profile: str = "ROOT_INTERLOCUTOR"
    sandbox_provider: Literal["daytona"] = "daytona"

    # Model fields read from DSPY_* env vars
    agent_model: str | None = Field(default=None, alias="DSPY_LM_MODEL")
    agent_delegate_model: str | None = Field(default=None, alias="DSPY_DELEGATE_LM_MODEL")
    agent_delegate_small_model: str | None = Field(default=None, alias="DSPY_DELEGATE_LM_SMALL_MODEL")
    agent_delegate_max_tokens: int = Field(default=64000, alias="DSPY_DELEGATE_LM_MAX_TOKENS")
    # Planner LM generation guardrails. ``planner_max_tokens`` caps a single
    # planner generation (the RLM/LongCoT loop) so one response can't run
    # unbounded; ``planner_lm_timeout_s`` bounds the per-request wall-clock so a
    # stalled provider can't hold a chat turn for minutes (see trace
    # tr-52a8d5b5d13d43ac102f7aba2aca9f58: one glm-5.2 call took 156s for a
    # 1.2k-char output). ``planner_temperature`` is optional; None leaves it to
    # the provider/model default. ``DSPY_LM_MAX_TOKENS`` is reused for the
    # planner cap to match the legacy ``_planner_lm_kwargs`` env-var convention.
    planner_max_tokens: int = Field(default=64000, alias="DSPY_LM_MAX_TOKENS")
    planner_lm_timeout_s: float = Field(default=60.0, alias="DSPY_PLANNER_LM_TIMEOUT_S")
    planner_temperature: float | None = Field(default=None, alias="DSPY_PLANNER_LM_TEMPERATURE")
    delegate_lm_timeout_s: float = Field(default=60.0, alias="DSPY_DELEGATE_LM_TIMEOUT_S")

    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    database_admin_url: str | None = Field(default=None, alias="DATABASE_ADMIN_URL")
    database_required: bool = False
    db_echo: bool = False
    db_validate_on_startup: bool = False
    allow_debug_auth: bool = False
    allow_query_auth_tokens: bool = False
    cors_allowed_origins: list[str] | str = Field(default_factory=list)
    ws_execution_max_queue: int = 256
    ws_execution_drop_policy: Literal["drop_oldest", "drop_newest"] = "drop_oldest"
    neon_auth_url: str | None = Field(
        default="https://ep-broad-water-al4k5bh7.neonauth.c-3.eu-central-1.aws.neon.tech/neondb/auth",
        alias="NEON_AUTH_URL",
    )
    neon_tenant_claim: str = Field(default="default", alias="NEON_TENANT_CLAIM")
    secret_encryption_key: str | None = Field(default=None, alias="FLEET_SECRET_ENCRYPTION_KEY")
    auth_required: bool = True
    serve_ui: bool = Field(default=True, alias="FLEET_RLM_SERVE_UI")
    expose_docs: bool = Field(default=False, alias="FLEET_RLM_EXPOSE_DOCS")
    expose_root: bool = Field(default=False, alias="FLEET_RLM_EXPOSE_ROOT")

    @field_validator(
        "agent_model",
        "agent_delegate_model",
        "agent_delegate_small_model",
        mode="after",
    )
    @classmethod
    def _validate_model_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None

        normalized = value.strip()
        if not normalized:
            return None

        # Bare model ids (no provider prefix) are valid at this layer. Custom
        # OpenAI-/Anthropic-compatible endpoints resolve them with an explicit
        # provider hint + api_base at the LLM-profile layer (see
        # integrations/llm_profiles/resolver.py::build_lm_kwargs_from_resolved),
        # which is the only place that knows whether a provider will be supplied.
        # ServerRuntimeConfig has no api_base/provider context, so rejecting bare
        # ids here would forbid configs the runtime legitimately supports.
        return normalized

    @classmethod
    def from_app_config(cls, config: AppConfig) -> ServerRuntimeConfig:
        """Build server runtime settings from the shared application config."""
        database_url = config.database.url or os.getenv("DATABASE_URL")
        database_admin_url = config.database.admin_url or os.getenv("DATABASE_ADMIN_URL")
        database_required = config.database.required
        if "DATABASE_REQUIRED" not in os.environ:
            database_required = True

        kwargs: dict = {
            "secret_name": config.sandbox.secret_name,
            "volume_name": resolve_server_volume_name(config),
            "timeout": config.sandbox.timeout,
            "react_max_iters": config.rlm_settings.max_iters,
            "deep_react_max_iters": config.rlm_settings.deep_max_iters,
            "enable_adaptive_iters": config.rlm_settings.enable_adaptive_iters,
            "rlm_max_iterations": config.llm.rlm_max_iterations,
            "rlm_max_llm_calls": config.rlm_settings.max_llm_calls,
            "rlm_action_max_tokens": config.rlm_settings.action_max_tokens,
            "rlm_max_depth": config.rlm_settings.max_depth,
            "rlm_child_isolation_mode": config.rlm_settings.child_isolation_mode,
            "rlm_child_fork_fallback": config.rlm_settings.child_fork_fallback,
            "delegate_max_calls_per_turn": config.rlm_settings.delegate_max_calls_per_turn,
            "delegate_result_truncation_chars": config.rlm_settings.delegate_result_truncation_chars,
            "delegate_execution_timeout": config.rlm_settings.delegate_execution_timeout,
            "delegate_max_iterations": config.rlm_settings.delegate_max_iterations,
            "delegate_adapter": config.rlm_settings.delegate_adapter,
            "daytona_broker_health_timeout": config.rlm_settings.daytona_broker_health_timeout,
            "daytona_broker_tool_call_timeout": config.rlm_settings.daytona_broker_tool_call_timeout,
            "daytona_broker_start_retries": config.rlm_settings.daytona_broker_start_retries,
            "interpreter_async_execute": config.sandbox.async_execute,
            "agent_guardrail_mode": config.llm.guardrail_mode,
            "agent_min_substantive_chars": config.llm.min_substantive_chars,
            "agent_max_output_chars": config.rlm_settings.max_output_chars,
            "agent_model": config.llm.model,
            "agent_delegate_model": config.llm.delegate_model,
            "agent_delegate_small_model": config.llm.delegate_small_model,
            "agent_delegate_max_tokens": config.llm.delegate_max_tokens,
            "database_url": database_url,
            "database_admin_url": database_admin_url,
            "database_required": database_required,
            "db_echo": config.database.echo,
            "db_validate_on_startup": config.database.validate_on_startup,
        }
        return cls(**kwargs)

    @computed_field
    @property
    def cors_origins_list(self) -> list[str]:
        """Return CORS origins as a normalized list."""
        return list(self.cors_allowed_origins)

    @field_validator(
        "cors_allowed_origins",
        mode="before",
    )
    @classmethod
    def _normalize_string_list(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        field_name = (info.field_name or "value").upper()
        raise ValueError(f"{field_name} must be provided as a comma-separated string or list")

    @model_validator(mode="before")
    @classmethod
    def _apply_env_aware_defaults(cls, values: dict) -> dict:
        """Apply cross-field defaults that depend on app_env."""
        values.pop("sandbox_provider", None)
        values.pop("SANDBOX_PROVIDER", None)
        app_env = str(values.get("app_env") or values.get("APP_ENV") or os.getenv("APP_ENV") or "local").strip().lower()

        # database_required defaults to True in staging/production.
        if "database_required" not in values and "DATABASE_REQUIRED" not in values:
            values["database_required"] = app_env in {"staging", "production"}

        # allow_debug_auth defaults to True only in local
        if "allow_debug_auth" not in values and "ALLOW_DEBUG_AUTH" not in values:
            values["allow_debug_auth"] = app_env == "local"

        # cors_allowed_origins defaults to "*" in local
        if "cors_allowed_origins" not in values and "CORS_ALLOWED_ORIGINS" not in values:
            values["cors_allowed_origins"] = ["*"] if app_env == "local" else []

        # serve_ui defaults to True in local, False in staging/production.
        # This keeps `fleet web` working while producing an API-only build
        # for managed hosts (e.g., FastAPI Cloud).
        if "serve_ui" not in values and "FLEET_RLM_SERVE_UI" not in values:
            values["serve_ui"] = app_env == "local"

        if "expose_docs" not in values and "FLEET_RLM_EXPOSE_DOCS" not in values:
            values["expose_docs"] = app_env == "local"

        if "expose_root" not in values and "FLEET_RLM_EXPOSE_ROOT" not in values:
            values["expose_root"] = app_env == "local"

        # auth_required defaults to True in staging/production, False in local
        if "auth_required" not in values and "AUTH_REQUIRED" not in values:
            values["auth_required"] = app_env in {"staging", "production"}

        return values

    def validate_startup_or_raise(self) -> None:
        """Validate environment guardrails before server startup."""
        if self.ws_execution_max_queue <= 0:
            raise ValueError("WS execution queue size must be > 0")

        if self.app_env == "local" and _looks_like_managed_runtime():
            raise ValueError(
                "Managed deployment detected with APP_ENV=local. Set APP_ENV to "
                "'staging' or 'production' and configure managed-host settings "
                "(for example: FLEET_RLM_SERVE_UI=false, DATABASE_REQUIRED/DATABASE_URL, "
                "AUTH_REQUIRED, and MLFLOW_ENABLED=false)."
            )

        if self.database_required and not self.database_url:
            raise ValueError("DATABASE_URL is required when database_required=true")

        if self.app_env in {"staging", "production"}:
            if not self.auth_required:
                raise ValueError("AUTH_REQUIRED must be true when APP_ENV is staging/production")
            if self.allow_debug_auth:
                raise ValueError("ALLOW_DEBUG_AUTH must be false when APP_ENV is staging/production")
            if "*" in self.cors_origins_list:
                raise ValueError("CORS_ALLOWED_ORIGINS cannot contain '*' in staging/production")
            if not (self.secret_encryption_key or "").strip():
                raise ValueError("FLEET_SECRET_ENCRYPTION_KEY is required for hosted Neon Auth BYOK profiles")

        # Neon Auth validation (required when auth_required=True)
        if self.auth_required:
            if not self.database_required:
                raise ValueError("DATABASE_REQUIRED must be true when AUTH_REQUIRED is true")
            if not self.neon_auth_url:
                raise ValueError("NEON_AUTH_URL is required when AUTH_REQUIRED is true")
            if not self.neon_tenant_claim.strip():
                raise ValueError("NEON_TENANT_CLAIM is required when AUTH_REQUIRED is true")
