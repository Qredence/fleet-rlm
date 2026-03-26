"""Server runtime configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from fleet_rlm.integrations.config.runtime_settings import resolve_env_path


def _resolve_server_env_path() -> Path:
    """Resolve a stable .env path for server runtime settings."""
    return resolve_env_path(
        start_paths=[
            Path(__file__).resolve().parent,
            Path.cwd(),
        ]
    )


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
    sandbox_provider: str = "modal"

    # Model fields read from DSPY_* env vars
    agent_model: str | None = Field(default=None, alias="DSPY_LM_MODEL")
    agent_delegate_model: str | None = Field(
        default=None, alias="DSPY_DELEGATE_LM_MODEL"
    )
    agent_delegate_small_model: str | None = Field(
        default=None, alias="DSPY_DELEGATE_LM_SMALL_MODEL"
    )
    agent_delegate_max_tokens: int = Field(
        default=64000, alias="DSPY_DELEGATE_LM_MAX_TOKENS"
    )

    database_url: str | None = Field(default=None, alias="DATABASE_URL")
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
    entra_issuer_template: str = "https://login.microsoftonline.com/{tenantid}/v2.0"
    entra_audience: str | None = None

    @computed_field
    @property
    def cors_origins_list(self) -> list[str]:
        """Return CORS origins as a normalized list."""
        return list(self.cors_allowed_origins)

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _normalize_cors_allowed_origins(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        raise TypeError("cors_allowed_origins must be provided as a string or list")

    @model_validator(mode="before")
    @classmethod
    def _apply_env_aware_defaults(cls, values: dict) -> dict:
        """Apply cross-field defaults that depend on app_env and auth_mode."""
        app_env = (
            str(
                values.get("app_env")
                or values.get("APP_ENV")
                or os.getenv("APP_ENV")
                or "local"
            )
            .strip()
            .lower()
        )
        auth_mode = (
            str(
                values.get("auth_mode")
                or values.get("AUTH_MODE")
                or os.getenv("AUTH_MODE")
                or "dev"
            )
            .strip()
            .lower()
        )

        # database_required defaults to True in staging/production
        if "database_required" not in values and "DATABASE_REQUIRED" not in values:
            values["database_required"] = app_env in {"staging", "production"}

        # allow_debug_auth defaults to True only in local
        if "allow_debug_auth" not in values and "ALLOW_DEBUG_AUTH" not in values:
            values["allow_debug_auth"] = app_env == "local"

        # allow_query_auth_tokens defaults based on env and auth_mode
        if (
            "allow_query_auth_tokens" not in values
            and "ALLOW_QUERY_AUTH_TOKENS" not in values
        ):
            values["allow_query_auth_tokens"] = (
                app_env == "local" or auth_mode == "entra"
            )

        # cors_allowed_origins defaults to "*" in local
        if (
            "cors_allowed_origins" not in values
            and "CORS_ALLOWED_ORIGINS" not in values
        ):
            values["cors_allowed_origins"] = ["*"] if app_env == "local" else []

        # auth_required defaults to True when auth_mode is entra
        if "auth_required" not in values and "AUTH_REQUIRED" not in values:
            values["auth_required"] = auth_mode == "entra"

        # entra_issuer_template fallback to ENTRA_ISSUER
        if (
            "entra_issuer_template" not in values
            and "ENTRA_ISSUER_TEMPLATE" not in values
        ):
            entra_issuer = values.get("ENTRA_ISSUER") or os.getenv("ENTRA_ISSUER")
            if entra_issuer:
                values["entra_issuer_template"] = entra_issuer

        return values

    def validate_startup_or_raise(self) -> None:
        """Validate environment guardrails before server startup."""
        if self.ws_execution_max_queue <= 0:
            raise ValueError("WS execution queue size must be > 0")

        if self.database_required and not self.database_url:
            raise ValueError("DATABASE_URL is required when database_required=true")

        if self.app_env in {"staging", "production"}:
            if not self.auth_required:
                raise ValueError(
                    "AUTH_REQUIRED must be true when APP_ENV is staging/production"
                )
            if self.allow_debug_auth:
                raise ValueError(
                    "ALLOW_DEBUG_AUTH must be false when APP_ENV is staging/production"
                )
            if self.allow_query_auth_tokens and self.auth_mode != "entra":
                raise ValueError(
                    "ALLOW_QUERY_AUTH_TOKENS must be false when APP_ENV is staging/production"
                )
            if "*" in self.cors_origins_list:
                raise ValueError(
                    "CORS_ALLOWED_ORIGINS cannot contain '*' in staging/production"
                )
            if self.auth_mode == "dev" and self.dev_jwt_secret == "change-me":
                raise ValueError(
                    "DEV_JWT_SECRET must be customized for staging/production in AUTH_MODE=dev"
                )

        if self.auth_mode == "entra":
            if not self.auth_required:
                raise ValueError("AUTH_REQUIRED must be true when AUTH_MODE=entra")
            if not self.database_required:
                raise ValueError("DATABASE_REQUIRED must be true when AUTH_MODE=entra")
            if not self.entra_jwks_url:
                raise ValueError("ENTRA_JWKS_URL is required when AUTH_MODE=entra")
            if not self.entra_audience:
                raise ValueError("ENTRA_AUDIENCE is required when AUTH_MODE=entra")
            if "{tenantid}" not in self.entra_issuer_template:
                raise ValueError(
                    "ENTRA_ISSUER_TEMPLATE must contain the {tenantid} placeholder when AUTH_MODE=entra"
                )
