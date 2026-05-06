"""Pydantic request/response schemas for the FastAPI server."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .volumes import VolumeProvider

ExecutionMode = Literal["auto", "rlm_only", "tools_only"]


class RuntimeSettingsSnapshot(BaseModel):
    """Current runtime settings snapshot returned by the Settings API."""

    env_path: str = Field(description="Filesystem path to the environment file being edited.")
    keys: list[str] = Field(
        default_factory=list,
        description="Ordered list of runtime setting keys surfaced by the Settings API.",
    )
    values: dict[str, str] = Field(
        default_factory=dict,
        description="Unmasked runtime setting values that are safe to return directly.",
    )
    masked_values: dict[str, str] = Field(
        default_factory=dict,
        description="Masked secret values returned for display-only settings fields.",
    )


class RuntimeSettingsUpdateRequest(BaseModel):
    """Patch body for runtime setting updates."""

    updates: dict[str, Any] = Field(
        default_factory=dict,
        description="Mapping of allowlisted runtime setting keys to their new values.",
    )


class RuntimeSettingsUpdateResponse(BaseModel):
    """Result payload after runtime settings are persisted and hot-applied."""

    updated: list[str] = Field(
        default_factory=list,
        description="Runtime setting keys that were successfully updated.",
    )
    env_path: str = Field(description="Filesystem path to the environment file that was updated.")


class RuntimeConnectivityTestResponse(BaseModel):
    """Result payload for runtime connectivity and preflight diagnostics."""

    kind: Literal["lm", "daytona"] = Field(description="Runtime subsystem that was tested.")
    ok: bool = Field(description="Whether the connectivity test completed successfully.")
    preflight_ok: bool = Field(description="Whether prerequisite configuration checks passed.")
    checked_at: str = Field(description="UTC timestamp when the test completed.")
    checks: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured boolean or value checks collected during the test run.",
    )
    guidance: list[str] = Field(
        default_factory=list,
        description="Human-readable remediation steps when the test did not pass cleanly.",
    )
    latency_ms: int | None = Field(
        default=None,
        description="Observed latency for the successful smoke test, when applicable.",
    )
    output_preview: str | None = Field(
        default=None,
        description="Short preview of the smoke-test output, when available.",
    )
    error: str | None = Field(
        default=None,
        description="Error summary when the test failed.",
    )


class RuntimeTestCache(BaseModel):
    """Cached runtime test results included in the runtime status payload."""

    lm: RuntimeConnectivityTestResponse | None = Field(
        default=None,
        description="Most recent language-model connectivity test result, if one has been run.",
    )
    daytona: RuntimeConnectivityTestResponse | None = Field(
        default=None,
        description="Most recent Daytona connectivity test result, if one has been run.",
    )


class RuntimeActiveModels(BaseModel):
    """Resolved active model identifiers currently loaded by the runtime."""

    planner: str = Field(default="", description="Planner model identifier currently in use.")
    delegate: str = Field(default="", description="Delegate model identifier currently in use.")
    delegate_small: str = Field(
        default="",
        description="Small delegate model identifier currently in use, when configured.",
    )


class RuntimeStatusResponse(BaseModel):
    """Combined readiness and diagnostics snapshot for the runtime settings UI."""

    app_env: str = Field(description="Current application environment, such as `local` or `prod`.")
    write_enabled: bool = Field(description="Whether runtime settings writes are currently allowed.")
    ready: bool = Field(description="Whether critical runtime services are ready to serve requests.")
    active_models: RuntimeActiveModels = Field(description="Resolved planner and delegate model identities.")
    sandbox_provider: VolumeProvider = Field(
        default="daytona",
        description="Active sandbox backend selected for runtime execution and volume browsing.",
    )
    llm: dict[str, Any] = Field(
        default_factory=dict,
        description="Language-model configuration and readiness diagnostics.",
    )
    mlflow: dict[str, Any] = Field(
        default_factory=dict,
        description="MLflow enablement and startup diagnostics.",
    )
    daytona: dict[str, Any] = Field(
        default_factory=dict,
        description="Daytona configuration and readiness diagnostics.",
    )
    tests: RuntimeTestCache = Field(description="Cached runtime connectivity test results exposed in the Settings UI.")
    guidance: list[str] = Field(
        default_factory=list,
        description="Human-readable remediation steps for incomplete runtime setup.",
    )
