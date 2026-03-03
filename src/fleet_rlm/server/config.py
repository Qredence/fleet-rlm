"""Server runtime configuration."""

from __future__ import annotations

import os
from typing import Literal, cast

from pydantic import BaseModel, Field

from fleet_rlm._env_utils import (
    env_bool as _env_bool,
    env_csv as _env_csv,
    env_int as _env_int,
)


def _env_app_env() -> str:
    return (os.getenv("APP_ENV") or "local").strip().lower()


class ServerRuntimeConfig(BaseModel):
    app_env: Literal["local", "staging", "production"] = cast(
        Literal["local", "staging", "production"],
        _env_app_env(),
    )
    secret_name: str = "LITELLM"
    volume_name: str | None = Field(
        default_factory=lambda: os.getenv("VOLUME_NAME") or None
    )
    timeout: int = 900
    react_max_iters: int = 15
    deep_react_max_iters: int = 35
    enable_adaptive_iters: bool = True
    rlm_max_iterations: int = 30
    rlm_max_llm_calls: int = 50
    rlm_max_depth: int = 2  # Maximum recursion depth for sub-agents (rlm_query)
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
    sandbox_provider: str = "modal"  # "modal", "local", "daytona"
    agent_model: str | None = None  # Model identifier to use for the agent
    agent_delegate_model: str | None = Field(
        default_factory=lambda: os.getenv("DSPY_DELEGATE_LM_MODEL") or None
    )
    agent_delegate_small_model: str | None = Field(
        default_factory=lambda: os.getenv("DSPY_DELEGATE_LM_SMALL_MODEL") or None
    )
    agent_delegate_max_tokens: int = Field(
        default_factory=lambda: _env_int(
            os.getenv("DSPY_DELEGATE_LM_MAX_TOKENS"), default=64000
        )
    )
    database_url: str | None = Field(
        default_factory=lambda: os.getenv("DATABASE_URL") or None
    )
    database_required: bool = Field(
        default_factory=lambda: _env_bool(
            os.getenv("DATABASE_REQUIRED"),
            default=_env_app_env() in {"staging", "production"},
        )
    )
    db_echo: bool = False
    db_validate_on_startup: bool = False
    allow_debug_auth: bool = Field(
        default_factory=lambda: _env_bool(
            os.getenv("ALLOW_DEBUG_AUTH"),
            default=_env_app_env() == "local",
        )
    )
    allow_query_auth_tokens: bool = Field(
        default_factory=lambda: _env_bool(
            os.getenv("ALLOW_QUERY_AUTH_TOKENS"),
            default=_env_app_env() == "local",
        )
    )
    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: _env_csv(
            os.getenv("CORS_ALLOWED_ORIGINS"),
            default=["*"] if _env_app_env() == "local" else [],
        )
    )
    ws_execution_max_queue: int = Field(
        default_factory=lambda: _env_int(
            os.getenv("WS_EXECUTION_MAX_QUEUE"), default=256
        )
    )
    ws_execution_drop_policy: Literal["drop_oldest", "drop_newest"] = Field(
        default_factory=lambda: (
            (os.getenv("WS_EXECUTION_DROP_POLICY") or "drop_oldest").strip().lower()
        )
    )
    auth_mode: Literal["dev", "entra"] = Field(
        default_factory=lambda: (os.getenv("AUTH_MODE") or "dev").strip().lower()
    )
    auth_required: bool = Field(
        default_factory=lambda: _env_bool(
            os.getenv("AUTH_REQUIRED"),
            default=((os.getenv("AUTH_MODE") or "dev").strip().lower() == "entra"),
        )
    )
    dev_jwt_secret: str = Field(
        default_factory=lambda: os.getenv("DEV_JWT_SECRET") or "change-me"
    )
    entra_jwks_url: str | None = Field(
        default_factory=lambda: os.getenv("ENTRA_JWKS_URL") or None
    )
    entra_issuer: str | None = Field(
        default_factory=lambda: os.getenv("ENTRA_ISSUER") or None
    )
    entra_audience: str | None = Field(
        default_factory=lambda: os.getenv("ENTRA_AUDIENCE") or None
    )

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
            if self.allow_query_auth_tokens:
                raise ValueError(
                    "ALLOW_QUERY_AUTH_TOKENS must be false when APP_ENV is staging/production"
                )
            if "*" in self.cors_allowed_origins:
                raise ValueError(
                    "CORS_ALLOWED_ORIGINS cannot contain '*' in staging/production"
                )
            if self.auth_mode == "dev" and self.dev_jwt_secret == "change-me":
                raise ValueError(
                    "DEV_JWT_SECRET must be customized for staging/production in AUTH_MODE=dev"
                )
