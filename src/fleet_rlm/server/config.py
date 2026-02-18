"""Server runtime configuration."""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field


class ServerRuntimeConfig(BaseModel):
    secret_name: str = "LITELLM"
    volume_name: str | None = None
    timeout: int = 900
    react_max_iters: int = 5
    rlm_max_iterations: int = 30
    rlm_max_llm_calls: int = 50
    rlm_max_depth: int = 2  # Maximum recursion depth for sub-agents (rlm_query)
    interpreter_async_execute: bool = True
    agent_guardrail_mode: Literal["off", "warn", "strict"] = "off"
    agent_min_substantive_chars: int = 20
    agent_max_output_chars: int = 10000
    ws_default_workspace_id: str = "default"
    ws_default_user_id: str = "anonymous"
    ws_enforce_react_interlocutor: bool = True
    ws_default_execution_profile: str = "ROOT_INTERLOCUTOR"
    agent_model: str | None = None  # Model identifier to use for the agent
    database_url: str | None = Field(
        default_factory=lambda: os.getenv("DATABASE_URL") or None
    )
    db_echo: bool = False
    db_validate_on_startup: bool = False
    auth_mode: Literal["dev", "entra"] = Field(
        default_factory=lambda: (os.getenv("AUTH_MODE") or "dev").strip().lower()
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
