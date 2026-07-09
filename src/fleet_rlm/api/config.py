"""Server runtime configuration.

``AppConfig`` is the single config model for the server runtime. It is a
``BaseSettings`` subclass that reads from environment variables (and an
optional ``.env`` file). The CLI/Hydra ``AppConfig`` defined in
``integrations/config/env.py`` is a separate nested model used for YAML
configuration; the server uses this env-var-backed ``AppConfig`` directly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import (
    Field,
    ValidationInfo,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from fleet_rlm.api.runtime_services.execution_backend import ExecutionBackend
from fleet_rlm.integrations.config.runtime_settings import resolve_env_path

DEFAULT_SERVER_VOLUME_NAME = "rlm-volume-dspy"


def _resolve_server_env_path() -> Path:
    """Resolve a stable .env path for server runtime settings."""
    return resolve_env_path(
        start_paths=[
            Path(__file__).resolve().parent,
            Path.cwd(),
        ]
    )


def resolve_server_volume_name(volume_name: str | None) -> str | None:
    """Resolve the server-side volume name from a configured volume name."""
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


class AppConfig(BaseSettings):
    """Server runtime configuration loaded from environment variables.

    This is the single config model for the server runtime. Fields are
    automatically populated from environment variables matching the field
    name (case-insensitive).  For example, ``app_env`` reads from
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
    rlm_max_iterations: int = 15
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
    execution_backend: ExecutionBackend = Field(
        default=ExecutionBackend.legacy_agent_runtime,
        alias="EXECUTION_BACKEND",
    )

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
    cors_allowed_origins: list[str] | str = Field(default_factory=list)
    ws_execution_max_queue: int = 256
    ws_execution_drop_policy: Literal["drop_oldest", "drop_newest"] = "drop_oldest"
    neon_tenant_claim: str | None = Field(default=None, alias="NEON_TENANT_CLAIM")
    secret_encryption_key: str | None = Field(default=None, alias="FLEET_SECRET_ENCRYPTION_KEY")
    auth_required: bool = True
    auth_mode: Literal["dev", "entra", "neon"] = Field(default="dev", alias="AUTH_MODE")
    dev_jwt_secret: str = Field(default="change-me", alias="DEV_JWT_SECRET")
    entra_jwks_url: str | None = Field(default=None, alias="ENTRA_JWKS_URL")
    entra_audience: str | None = Field(default=None, alias="ENTRA_AUDIENCE")
    entra_issuer_url: str | None = Field(default=None, alias="ENTRA_ISSUER_URL")
    entra_issuer_template: str | None = Field(default=None, alias="ENTRA_ISSUER_TEMPLATE")
    entra_allowed_user_ids: list[str] = Field(default_factory=list, alias="ENTRA_ALLOWED_USER_IDS")
    entra_allowed_group_ids: list[str] = Field(default_factory=list, alias="ENTRA_ALLOWED_GROUP_IDS")
    serve_ui: bool = Field(default=True, alias="FLEET_RLM_SERVE_UI")
    expose_docs: bool = Field(default=False, alias="FLEET_RLM_EXPOSE_DOCS")
    expose_root: bool = Field(default=False, alias="FLEET_RLM_EXPOSE_ROOT")
    skill_remote_url_install_enabled: bool = Field(default=False, alias="FLEET_SKILL_REMOTE_URL_INSTALL")
    skill_remote_bundle_install_enabled: bool = Field(default=False, alias="FLEET_SKILL_REMOTE_BUNDLE_INSTALL")
    skill_remote_allowed_hosts: list[str] = Field(default_factory=list, alias="FLEET_SKILL_REMOTE_ALLOWED_HOSTS")
    skill_remote_tap_url: str | None = Field(default=None, alias="FLEET_SKILL_REMOTE_TAP_URL")

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
        # AppConfig has no api_base/provider context, so rejecting bare
        # ids here would forbid configs the runtime legitimately supports.
        return normalized

    @computed_field
    @property
    def cors_origins_list(self) -> list[str]:
        """Return CORS origins as a normalized list."""
        return list(self.cors_allowed_origins)

    @field_validator(
        "cors_allowed_origins",
        "entra_allowed_user_ids",
        "entra_allowed_group_ids",
        "skill_remote_allowed_hosts",
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

        # database_required defaults to True in staging/production, or when
        # AUTH_REQUIRED is explicitly set (auth requires a database-backed tenant).
        if "database_required" not in values and "DATABASE_REQUIRED" not in values:
            if "auth_required" in values:
                auth_required_raw = str(values["auth_required"]).strip().lower()
            elif "AUTH_REQUIRED" in values:
                auth_required_raw = str(values["AUTH_REQUIRED"]).strip().lower()
            else:
                auth_required_raw = str(os.getenv("AUTH_REQUIRED") or "").strip().lower()
            auth_required_explicit = auth_required_raw in {"1", "true", "yes", "on"}
            values["database_required"] = app_env in {"staging", "production"} or auth_required_explicit

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

        # auth_required defaults to True in staging/production, False in local.
        # We parse potential string/env flags to guarantee a clean boolean state.
        if "auth_required" in values:
            auth_required_val = values["auth_required"]
        elif "AUTH_REQUIRED" in values:
            auth_required_val = values["AUTH_REQUIRED"]
        else:
            auth_required_val = None

        if auth_required_val is None:
            auth_required_raw = str(os.getenv("AUTH_REQUIRED") or "").strip().lower()
            if auth_required_raw:
                auth_required = auth_required_raw in {"1", "true", "yes", "on"}
            else:
                auth_required = app_env in {"staging", "production"}
        elif isinstance(auth_required_val, str):
            auth_required = auth_required_val.strip().lower() in {"1", "true", "yes", "on"}
        else:
            auth_required = bool(auth_required_val)

        values["auth_required"] = auth_required

        # Set default/override for auth_mode based on auth_required
        if "auth_mode" in values:
            auth_mode_val = values["auth_mode"]
        elif "AUTH_MODE" in values:
            auth_mode_val = values["AUTH_MODE"]
        else:
            auth_mode_val = None

        if not auth_required:
            values["auth_mode"] = "dev"
        elif auth_mode_val is None:
            values["auth_mode"] = "neon"

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
            if self.auth_mode == "dev":
                raise ValueError("AUTH_MODE=dev is not allowed when APP_ENV is staging/production")
            if "*" in self.cors_origins_list:
                raise ValueError("CORS_ALLOWED_ORIGINS cannot contain '*' in staging/production")
            if not (self.secret_encryption_key or "").strip():
                raise ValueError("FLEET_SECRET_ENCRYPTION_KEY is required for hosted Neon Auth BYOK profiles")

        # Neon Auth validation (required when auth_required=True and auth_mode=neon). The Neon Auth
        # URL itself is hardcoded as a class constant on NeonAuthProvider, so it
        # is not validated here; only the tenant claim and database backing.
        if self.auth_required:
            if not self.database_required:
                raise ValueError("DATABASE_REQUIRED must be true when AUTH_REQUIRED is true")
            if self.auth_mode == "neon" and not (self.neon_tenant_claim or "").strip():
                raise ValueError("NEON_TENANT_CLAIM is required when AUTH_REQUIRED is true and AUTH_MODE is neon")


# Backward-compatible alias — the class was renamed from ServerRuntimeConfig
# to AppConfig.  Keep the old name available so callers that still reference
# ``ServerRuntimeConfig`` (tests, bootstrap, dependencies, etc.) continue to
# work without an immediate migration.
ServerRuntimeConfig = AppConfig
