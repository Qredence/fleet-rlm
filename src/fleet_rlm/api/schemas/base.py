"""Pydantic request/response schemas for the FastAPI server."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from fleet_rlm import __version__


class HealthResponse(BaseModel):
    """Response body for the lightweight health endpoint."""

    ok: bool = Field(default=True, description="Whether the service reports itself as healthy.")
    version: str = Field(
        default=__version__,
        description="Package version currently serving the API.",
    )


class ReadyResponse(BaseModel):
    """Response body for the readiness endpoint."""

    ready: bool = Field(description="Whether critical startup dependencies are ready.")
    planner_configured: bool = Field(description="Whether a planner model is currently configured and available.")
    planner: Literal["ready", "missing"] = Field(description="Planner readiness classification.")
    database: Literal["ready", "missing", "disabled", "degraded"] = Field(
        description="Database readiness classification for persistence-backed features."
    )
    database_required: bool = Field(
        description="Whether the current server configuration requires database availability."
    )
    sandbox_provider: str = Field(description="Active sandbox backend selected for runtime execution.")


class AuthMeResponse(BaseModel):
    """Resolved identity payload returned to authenticated clients."""

    tenant_claim: str = Field(description="Tenant or workspace claim resolved from auth.")
    user_claim: str = Field(description="User claim resolved from auth.")
    email: str | None = Field(
        default=None,
        description="User email address when the auth provider returned one.",
    )
    name: str | None = Field(
        default=None,
        description="Display name returned by the auth provider, when available.",
    )
    tenant_id: str | None = Field(
        default=None,
        description="Persisted control-plane tenant identifier for admitted Entra users.",
    )
    user_id: str | None = Field(
        default=None,
        description="Persisted control-plane user identifier for admitted Entra users.",
    )


class ServiceInfoResponse(BaseModel):
    """Service capability and configuration surface returned by ``GET /api/v1/info``.

    Provides a single, stable snapshot of build metadata and active feature
    flags so clients and operators can inspect the running instance without
    tailing logs or querying multiple endpoints.
    """

    version: str = Field(
        default=__version__,
        description="Package version currently serving the API.",
    )
    app_env: Literal["local", "staging", "production"] = Field(description="Active deployment environment.")
    auth_mode: Literal["dev", "entra"] = Field(description="Authentication mode the server is running under.")
    auth_required: bool = Field(description="Whether authentication is enforced for all API requests.")
    sandbox_provider: str = Field(description="Active sandbox backend selected for runtime execution.")
    database_enabled: bool = Field(description="Whether a durable database backend is configured.")
    serve_ui: bool = Field(description="Whether the bundled React frontend is being served by this instance.")
    expose_docs: bool = Field(description="Whether the OpenAPI documentation UI (/docs, /redoc) is enabled.")
    agent_model: str | None = Field(
        default=None,
        description="Primary planner LM identifier when configured.",
    )
    rlm_max_depth: int = Field(description="Maximum recursive RLM child delegation depth.")
    rlm_max_iterations: int = Field(description="Maximum ReAct iterations per top-level run.")
