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
    volume_name = config.interpreter.volume_name
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
    rlm_max_depth: int = 2
    rlm_child_isolation_mode: Literal["auto", "context"] = Field(default="auto", alias="RLM_CHILD_ISOLATION_MODE")
    rlm_child_fork_fallback: Literal["clean", "fail"] = Field(default="clean", alias="RLM_CHILD_FORK_FALLBACK")
    delegate_max_calls_per_turn: int = 8
    delegate_result_truncation_chars: int = 8000
    interpreter_async_execute: bool = True
    agent_guardrail_mode: Literal["off", "warn", "strict"] = "off"
    agent_min_substantive_chars: int = 20
    agent_max_output_chars: int = 10000
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
    auth_mode: Literal["dev", "entra"] = "dev"
    auth_required: bool = False
    dev_jwt_secret: str = "change-me"
    entra_jwks_url: str | None = None
    entra_issuer_url: str | None = Field(default=None, alias="ENTRA_ISSUER_URL")
    entra_issuer_legacy: str | None = Field(
        default=None,
        alias="ENTRA_ISSUER",
        exclude=True,
        repr=False,
    )
    entra_issuer_template: str | None = "https://login.microsoftonline.com/{tenantid}/v2.0"
    entra_audience: str | None = None
    entra_allowed_user_ids: list[str] | str = Field(default_factory=list, alias="ENTRA_ALLOWED_USER_IDS")
    entra_allowed_group_ids: list[str] | str = Field(default_factory=list, alias="ENTRA_ALLOWED_GROUP_IDS")
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

        if "/" not in normalized:
            raise ValueError(
                f"LiteLLM model identifiers must include a provider prefix "
                f"(e.g. 'openai/gpt-4o' or 'anthropic/claude-3-5-sonnet'), got {value!r}"
            )
        return normalized

    @classmethod
    def from_app_config(cls, config: AppConfig) -> ServerRuntimeConfig:
        """Build server runtime settings from the shared application config."""
        kwargs: dict = {
            "secret_name": config.interpreter.secrets[0] if config.interpreter.secrets else "LITELLM",
            "volume_name": resolve_server_volume_name(config),
            "timeout": config.interpreter.timeout,
            "react_max_iters": config.rlm_settings.max_iters,
            "deep_react_max_iters": config.rlm_settings.deep_max_iters,
            "enable_adaptive_iters": config.rlm_settings.enable_adaptive_iters,
            "rlm_max_iterations": config.agent.rlm_max_iterations,
            "rlm_max_llm_calls": config.rlm_settings.max_llm_calls,
            "rlm_max_depth": config.rlm_settings.max_depth,
            "rlm_child_isolation_mode": config.rlm_settings.child_isolation_mode,
            "rlm_child_fork_fallback": config.rlm_settings.child_fork_fallback,
            "delegate_max_calls_per_turn": config.rlm_settings.delegate_max_calls_per_turn,
            "delegate_result_truncation_chars": config.rlm_settings.delegate_result_truncation_chars,
            "interpreter_async_execute": config.interpreter.async_execute,
            "agent_guardrail_mode": config.agent.guardrail_mode,
            "agent_min_substantive_chars": config.agent.min_substantive_chars,
            "agent_max_output_chars": config.rlm_settings.max_output_chars,
            "agent_model": config.agent.model,
            "agent_delegate_model": config.agent.delegate_model,
            "agent_delegate_max_tokens": config.agent.delegate_max_tokens,
            "db_validate_on_startup": True,
        }
        return cls(**kwargs)

    @computed_field
    @property
    def cors_origins_list(self) -> list[str]:
        """Return CORS origins as a normalized list."""
        return list(self.cors_allowed_origins)

    @computed_field
    @property
    def entra_allowed_user_ids_list(self) -> list[str]:
        """Return the configured Entra beta user allowlist."""
        return list(self.entra_allowed_user_ids)

    @computed_field
    @property
    def entra_allowed_group_ids_list(self) -> list[str]:
        """Return the configured Entra beta group allowlist."""
        return list(self.entra_allowed_group_ids)

    @field_validator(
        "cors_allowed_origins",
        "entra_allowed_user_ids",
        "entra_allowed_group_ids",
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

    @field_validator("entra_issuer_url", "entra_issuer_template", mode="before")
    @classmethod
    def _normalize_optional_string(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @model_validator(mode="before")
    @classmethod
    def _apply_env_aware_defaults(cls, values: dict) -> dict:
        """Apply cross-field defaults that depend on app_env and auth_mode."""
        values.pop("sandbox_provider", None)
        values.pop("SANDBOX_PROVIDER", None)
        app_env = str(values.get("app_env") or values.get("APP_ENV") or os.getenv("APP_ENV") or "local").strip().lower()
        auth_mode = (
            str(values.get("auth_mode") or values.get("AUTH_MODE") or os.getenv("AUTH_MODE") or "dev").strip().lower()
        )

        # database_required defaults to True in staging/production
        if "database_required" not in values and "DATABASE_REQUIRED" not in values:
            values["database_required"] = app_env in {"staging", "production"}

        # allow_debug_auth defaults to True only in local
        if "allow_debug_auth" not in values and "ALLOW_DEBUG_AUTH" not in values:
            values["allow_debug_auth"] = app_env == "local"

        # allow_query_auth_tokens defaults based on env and auth_mode
        if "allow_query_auth_tokens" not in values and "ALLOW_QUERY_AUTH_TOKENS" not in values:
            values["allow_query_auth_tokens"] = app_env == "local" or auth_mode == "entra"

        # cors_allowed_origins defaults to "*" in local
        if "cors_allowed_origins" not in values and "CORS_ALLOWED_ORIGINS" not in values:
            values["cors_allowed_origins"] = ["*"] if app_env == "local" else []

        # serve_ui defaults to True in local, False in staging/production.
        # This keeps `fleet web` working while producing an API-only build
        # for managed hosts (e.g., FastAPI Cloud).
        if "serve_ui" not in values and "FLEET_RLM_SERVE_UI" not in values:
            values["serve_ui"] = app_env == "local"

        if "expose_docs" not in values and "FLEET_RLM_EXPOSE_DOCS" not in values:
            values["expose_docs"] = app_env == "local" or (app_env == "staging" and auth_mode != "entra")

        if "expose_root" not in values and "FLEET_RLM_EXPOSE_ROOT" not in values:
            values["expose_root"] = app_env == "local" or (app_env == "staging" and auth_mode != "entra")

        # auth_required defaults to True when auth_mode is entra
        if "auth_required" not in values and "AUTH_REQUIRED" not in values:
            values["auth_required"] = auth_mode == "entra"

        explicit_issuer_template = values.get("entra_issuer_template") or values.get("ENTRA_ISSUER_TEMPLATE")
        explicit_issuer_url = values.get("entra_issuer_url") or values.get("ENTRA_ISSUER_URL")
        if explicit_issuer_url:
            values["entra_issuer_template"] = None
        if explicit_issuer_template and not explicit_issuer_url and "{tenantid}" not in str(explicit_issuer_template):
            values["entra_issuer_url"] = str(explicit_issuer_template).strip()
            values["entra_issuer_template"] = None

        # Backward-compatible fallback from ENTRA_ISSUER.
        if (
            "entra_issuer_url" not in values
            and "ENTRA_ISSUER_URL" not in values
            and "entra_issuer_template" not in values
            and "ENTRA_ISSUER_TEMPLATE" not in values
        ):
            entra_issuer = values.get("entra_issuer_legacy") or values.get("ENTRA_ISSUER")
            if entra_issuer:
                normalized_issuer = str(entra_issuer).strip()
                if "{tenantid}" in normalized_issuer:
                    values["entra_issuer_template"] = normalized_issuer
                else:
                    values["entra_issuer_url"] = normalized_issuer

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
            if self.allow_query_auth_tokens and self.auth_mode != "entra":
                raise ValueError("ALLOW_QUERY_AUTH_TOKENS must be false when APP_ENV is staging/production")
            if "*" in self.cors_origins_list:
                raise ValueError("CORS_ALLOWED_ORIGINS cannot contain '*' in staging/production")
            if self.auth_mode == "dev" and self.dev_jwt_secret == "change-me":
                raise ValueError("DEV_JWT_SECRET must be customized for staging/production in AUTH_MODE=dev")

        if self.auth_mode == "entra":
            if not self.auth_required:
                raise ValueError("AUTH_REQUIRED must be true when AUTH_MODE=entra")
            if not self.database_required:
                raise ValueError("DATABASE_REQUIRED must be true when AUTH_MODE=entra")
            if not self.entra_jwks_url:
                raise ValueError("ENTRA_JWKS_URL is required when AUTH_MODE=entra")
            if not self.entra_audience:
                raise ValueError("ENTRA_AUDIENCE is required when AUTH_MODE=entra")
            if self.entra_issuer_url:
                if "{tenantid}" in self.entra_issuer_url:
                    raise ValueError("ENTRA_ISSUER_URL must be a fixed issuer URL, not a template")
            elif not self.entra_issuer_template:
                raise ValueError("Set ENTRA_ISSUER_URL or ENTRA_ISSUER_TEMPLATE when AUTH_MODE=entra")
            elif "{tenantid}" not in self.entra_issuer_template:
                raise ValueError(
                    "ENTRA_ISSUER_TEMPLATE must contain the {tenantid} placeholder "
                    "when AUTH_MODE=entra; use ENTRA_ISSUER_URL for a single-tenant issuer"
                )
            if self.app_env in {"staging", "production"} and self.auth_mode == "entra":
                if self.expose_docs:
                    raise ValueError("FLEET_RLM_EXPOSE_DOCS must be false when AUTH_MODE=entra in staging/production")
                if self.expose_root:
                    raise ValueError("FLEET_RLM_EXPOSE_ROOT must be false when AUTH_MODE=entra in staging/production")
                if (
                    self.entra_issuer_url
                    and not self.entra_allowed_user_ids_list
                    and not self.entra_allowed_group_ids_list
                ):
                    raise ValueError(
                        "Single-tenant Entra deployments in staging/production "
                        "must configure ENTRA_ALLOWED_USER_IDS or ENTRA_ALLOWED_GROUP_IDS"
                    )
