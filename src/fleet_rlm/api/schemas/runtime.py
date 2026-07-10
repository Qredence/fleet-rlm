"""Pydantic request/response schemas for the FastAPI server."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .volumes import VolumeProvider

ExecutionMode = Literal["auto", "rlm_only", "tools_only"]


class RuntimeSettingsField(BaseModel):
    """Single display-safe runtime setting field."""

    key: str = Field(description="Environment variable key backing this setting.")
    label: str = Field(description="Human-readable setting label.")
    description: str = Field(description="Human-readable setting description.")
    value: str = Field(default="", description="Display-safe setting value.")
    masked_value: str = Field(default="", description="Masked display value for secret settings.")
    secret: bool = Field(default=False, description="Whether the field stores sensitive data.")
    editable: bool = Field(default=True, description="Whether the field can be patched through the Settings API.")
    reload_required: bool = Field(
        default=False, description="Whether applying this setting reloads runtime dependencies."
    )
    placeholder: str | None = Field(default=None, description="Optional UI placeholder.")
    default: str | None = Field(default=None, description="Optional default value displayed by settings clients.")


class RuntimeSettingsCategory(BaseModel):
    """Categorized group of runtime settings fields."""

    id: str = Field(description="Stable category identifier.")
    label: str = Field(description="Human-readable category label.")
    description: str = Field(description="Human-readable category description.")
    fields: list[RuntimeSettingsField] = Field(default_factory=list, description="Fields in this category.")


class RuntimeSettingsSnapshot(BaseModel):
    """Current runtime settings snapshot returned by the Settings API."""

    env_path: str = Field(description="Filesystem path to the environment file being edited.")
    categories: list[RuntimeSettingsCategory] = Field(
        default_factory=list,
        description="Categorized runtime setting fields surfaced by the Settings API.",
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
    skipped: list[str] = Field(
        default_factory=list,
        description="Runtime setting keys that were accepted in the request but not persisted (e.g. masked secret round-trips).",
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


class RuntimeMlflowStatus(BaseModel):
    """MLflow enablement and startup diagnostics for the runtime settings UI."""

    enabled: bool = Field(description="Whether MLflow tracing is enabled for this runtime.")
    tracking_uri: str = Field(default="", description="Configured MLflow tracking server URI.")
    experiment_name: str | None = Field(
        default=None,
        description="Configured MLflow experiment name.",
    )
    experiment_id: str | None = Field(
        default=None,
        description="Resolved MLflow experiment id when startup succeeded.",
    )
    auto_start_enabled: bool = Field(
        default=False,
        description="Whether the runtime may auto-start a local MLflow tracking server.",
    )
    auto_assessment_enabled: bool = Field(
        default=False,
        description="Whether Fleet-managed MLflow auto-assessment is enabled.",
    )
    persisted_scorer_count: int = Field(
        default=0,
        description="Count of persisted MLflow scorers active on the tracking server.",
    )
    persisted_scorers: list[str] = Field(
        default_factory=list,
        description="Names of persisted MLflow scorers active on the tracking server.",
    )
    startup_status: str = Field(
        default="pending",
        description="MLflow startup lifecycle status for this runtime.",
    )
    startup_error: str | None = Field(
        default=None,
        description="Startup error summary when MLflow initialization failed.",
    )


class RuntimeActiveModels(BaseModel):
    """Resolved active model identifiers currently loaded by the runtime."""

    planner: str = Field(default="", description="Planner model identifier currently in use.")
    delegate: str = Field(default="", description="Delegate model identifier currently in use.")
    delegate_small: str = Field(
        default="",
        description="Small delegate model identifier currently in use, when configured.",
    )
    planner_profile_id: str | None = Field(
        default=None,
        description="Provider profile id bound to the planner role, when configured.",
    )
    planner_profile_name: str | None = Field(
        default=None,
        description="Human-readable provider profile name for the planner role.",
    )
    delegate_profile_id: str | None = Field(
        default=None,
        description="Provider profile id bound to the delegate role, when configured.",
    )
    delegate_profile_name: str | None = Field(
        default=None,
        description="Human-readable provider profile name for the delegate role.",
    )
    delegate_small_profile_id: str | None = Field(
        default=None,
        description="Provider profile id bound to the delegate_small role, when configured.",
    )
    delegate_small_profile_name: str | None = Field(
        default=None,
        description="Human-readable provider profile name for the delegate_small role.",
    )


class RuntimeStatusResponse(BaseModel):
    """Combined readiness and diagnostics snapshot for the runtime settings UI."""

    app_env: str = Field(description="Current application environment, such as `local` or `prod`.")
    write_enabled: bool = Field(description="Whether runtime settings writes are currently allowed.")
    settings_write_enabled: bool = Field(description="Whether process/env runtime settings writes are allowed.")
    profile_write_enabled: bool = Field(description="Whether authenticated LLM provider profile writes are allowed.")
    ready: bool = Field(description="Whether critical runtime services are ready to serve requests.")
    execution_backend: Literal["legacy_agent_runtime", "direct_rlm"] = Field(
        description="Server-configured backend used for new chat turns."
    )
    active_models: RuntimeActiveModels = Field(description="Resolved planner and delegate model identities.")
    sandbox_provider: VolumeProvider = Field(
        default="daytona",
        description="Active sandbox backend selected for runtime execution and volume browsing.",
    )
    llm: dict[str, Any] = Field(
        default_factory=dict,
        description="Language-model configuration and readiness diagnostics.",
    )
    mlflow: RuntimeMlflowStatus = Field(
        default_factory=RuntimeMlflowStatus,
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
