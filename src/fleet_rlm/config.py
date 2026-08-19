"""Typed process settings for the Fleet RLM backend.

No clients, engines, LMs, or network access are constructed at import time.
Secrets use ``SecretStr`` so public dumps never expose plaintext values.
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
import urllib.parse
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, SecretStr, field_validator, model_validator

from fleet_rlm.snapshot_contract import validate_snapshot_name

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "fleet.toml"
_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


class FleetConfigurationError(ValueError):
    """Raised when the required Fleet runtime policy is invalid."""


EditorKind = Literal["text", "number", "boolean", "single_choice", "multi_choice", "string_list"]


@dataclass(frozen=True, slots=True)
class FleetFieldPolicy:
    """Authoritative policy declaration for one ``Settings`` field.

    Every ``Settings`` field carries exactly one declaration so the supported
    TOML location, operator grouping, editor affordances, and compatibility
    disposition live next to the field itself.  ``_TABLE_KEYS``, policy
    flattening, and the ``config_policy`` editor inventory derive from these
    declarations instead of mirroring field names by hand.

    ``toml_path=None`` marks fields that TOML cannot set: secrets resolved at
    the runtime seam and programmatic-only compatibility inputs.  Operator-
    editable fields must declare complete editor metadata plus a unique
    ``rank`` that pins the policy inventory ordering.
    """

    toml_path: str | None
    group: str | None = None
    label: str | None = None
    editor: EditorKind | None = None
    choices: tuple[str, ...] = ()
    rank: int | None = None
    required_in_policy: bool = False
    secret: bool = False
    compatibility: bool = False
    doc: str | None = None


@dataclass(frozen=True, slots=True)
class ProfileEnvironmentContract:
    """Non-secret provider and profile facts derived from ``config/fleet.toml``."""

    name: str
    runtime_environment: str
    provider: str
    root_model: str
    sub_model: str
    root_api_key_env: str
    sub_api_key_env: str
    root_base_url_env: str | None
    sub_base_url_env: str | None
    root_max_tokens: int | None
    sub_max_tokens: int | None
    daytona_api_key_env: str
    database_url_env: str | None
    mlflow_tracing_enabled: bool
    mlflow_tracking_uri: str | None
    mlflow_environment_names: tuple[str, ...]
    recursion_enabled: bool

    @property
    def provider_environment_names(self) -> tuple[str, ...]:
        """Return environment names needed for provider-backed execution."""
        return _unique_environment_names(
            self.daytona_api_key_env,
            self.root_api_key_env,
            self.sub_api_key_env,
            self.root_base_url_env,
            self.sub_base_url_env,
        )

    @property
    def managed_policy_environment_names(self) -> tuple[str, ...]:
        """Return provider plus explicitly required managed-policy environment names."""
        if self.name != "daytona-managed":
            return self.provider_environment_names
        return _unique_environment_names(
            *self.provider_environment_names,
            self.database_url_env,
            *self.mlflow_environment_names,
        )


class LLMRoleSettings(BaseModel):
    """Non-secret settings for one explicit Root or Sub Model role."""

    model_config = ConfigDict(extra="forbid")

    model: str
    api_key_env: str
    base_url: str | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = None
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    cache: bool = True
    num_retries: int = Field(default=3, ge=0)

    @field_validator("api_key_env")
    @classmethod
    def _validate_api_key_env(cls, value: str) -> str:
        """
        Validate an API key environment-variable name.

        Parameters:
            value (str): Environment-variable name to validate.

        Returns:
            str: The validated environment-variable name.

        Raises:
            ValueError: If the name is not uppercase or does not match the required format.
        """
        if not _ENVIRONMENT_NAME.fullmatch(value):
            raise ValueError("api_key_env must name an uppercase environment variable")
        return value


@dataclass(frozen=True, slots=True)
class LLMRoleBundle:
    """Explicitly resolved Root and Sub LM role settings."""

    root: LLMRoleSettings
    sub: LLMRoleSettings


class Settings(BaseModel):
    """Fully resolved Fleet runtime settings.

    Production callers must use :func:`load_runtime_settings`, which derives
    these values from the selected TOML policy and its explicit environment
    references. Direct ``Settings(...)`` construction is for tests and
    injected inventories: it accepts constructor values only and rejects
    unknown or retired keyword arguments rather than ignoring them silently.

    This model never scans ambient environment variables, ``.env``, or secret
    files for field names. That would let stale ``FLEET_*`` or unprefixed
    values such as ``DATABASE_URL`` override the selected TOML policy.

    Each field owns one authoritative :class:`FleetFieldPolicy` policy
    declaration. Unknown constructor/validation keys fail with a
    :class:`FleetConfigurationError` naming the unsupported keys only —
    values are never echoed, so a misspelled secret cannot leak.
    """

    model_config = ConfigDict(extra="forbid")

    def __init__(self, /, **data: Any) -> None:
        """Reject unknown keyword arguments without echoing their values."""
        self._reject_unknown_fields(data)
        super().__init__(**data)

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        extra: Any = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Settings:
        """Reject unknown field names without echoing their values."""
        if extra not in (None, "forbid"):
            raise FleetConfigurationError("Settings does not permit relaxed extra-field handling")
        if isinstance(obj, Mapping):
            cls._reject_unknown_fields(obj)
        return super().model_validate(
            obj,
            strict=strict,
            from_attributes=from_attributes,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )

    @classmethod
    def _reject_unknown_fields(cls, data: Mapping[Any, Any]) -> None:
        """Fail on unsupported keys, naming keys only to protect secret values."""
        unknown = sorted(key for key in data if key not in cls.model_fields)
        if unknown:
            raise FleetConfigurationError(f"unsupported Settings field(s): {', '.join(map(str, unknown))}")

    app_name: Annotated[
        str,
        FleetFieldPolicy(
            toml_path="application.name",
            group="Application",
            label="Name",
            editor="text",
            rank=0,
            required_in_policy=True,
        ),
    ] = Field(default="fleet-rlm")
    daytona_api_key: Annotated[
        SecretStr | None,
        FleetFieldPolicy(
            toml_path=None, secret=True, doc="Provider credential resolved at runtime from daytona.api_key_env"
        ),
    ] = Field(default=None)
    daytona_snapshot: Annotated[
        str | None,
        FleetFieldPolicy(toml_path="daytona.snapshot", group="Daytona", label="Snapshot", editor="text", rank=45),
    ] = Field(default=None)
    daytona_org_id: Annotated[
        str | None,
        FleetFieldPolicy(toml_path="daytona.org_id", group="Daytona", label="Organization ID", editor="text", rank=46),
    ] = Field(default=None)
    llm_api_key: Annotated[
        SecretStr | None,
        FleetFieldPolicy(
            toml_path=None,
            secret=True,
            compatibility=True,
            doc="Legacy programmatic fallback key for roles naming FLEET_OPENAI_API_KEY; never settable via TOML",
        ),
    ] = Field(default=None)
    llm_base_url: Annotated[
        str | None,
        FleetFieldPolicy(
            toml_path=None, compatibility=True, doc="Programmatic-only compatibility input; no TOML mapping"
        ),
    ] = Field(
        default=None,
        description="Optional OpenAI-compatible base URL for dspy.LM",
    )
    llm_max_tokens: Annotated[
        int | None,
        FleetFieldPolicy(
            toml_path=None, compatibility=True, doc="Programmatic-only compatibility input; no TOML mapping"
        ),
    ] = Field(
        default=None,
        ge=1,
        description="Optional output-token limit passed to both DSPy model roles",
    )
    root_model: Annotated[
        str,
        FleetFieldPolicy(
            toml_path="llm.root.model",
            group="Root LLM",
            label="Model id",
            editor="text",
            rank=7,
            required_in_policy=True,
        ),
    ] = Field(
        default="openai/gpt-4o-mini",
        description="Root model id sent through the OpenAI-compatible Chat Completion provider",
    )
    sub_model: Annotated[
        str,
        FleetFieldPolicy(
            toml_path="llm.sub.model",
            group="Sub LLM",
            label="Model id",
            editor="text",
            rank=16,
            required_in_policy=True,
        ),
    ] = Field(
        default="openai/gpt-4o-mini",
        description="Sub model id sent through the OpenAI-compatible Chat Completion provider",
    )
    database_url: Annotated[
        str | None,
        FleetFieldPolicy(
            toml_path=None, doc="Resolved at runtime from the environment named by storage.database_url_env"
        ),
    ] = Field(
        default=None,
        description="Async SQLAlchemy URL (e.g. sqlite+aiosqlite:///:memory: or postgresql+asyncpg://...)",
    )
    volume_name: Annotated[
        str,
        FleetFieldPolicy(
            toml_path="daytona.volume_name",
            group="Daytona",
            label="Volume name",
            editor="text",
            rank=47,
            required_in_policy=True,
        ),
    ] = Field(
        default="rlm-volume-dspy",
        description="Daytona Volume name for workspace durable files",
    )
    volume_mount_path: Annotated[
        str,
        FleetFieldPolicy(
            toml_path="daytona.volume_mount_path",
            group="Daytona",
            label="Volume mount path",
            editor="text",
            rank=48,
            required_in_policy=True,
        ),
    ] = Field(
        default="/home/daytona/fleet",
        description="Absolute Sandbox mount path for the workspace Volume",
    )
    run_environment: Annotated[
        Literal["daytona"],
        FleetFieldPolicy(
            toml_path="runtime.environment",
            group="Runtime",
            label="Environment",
            editor="single_choice",
            choices=("daytona",),
            rank=1,
            required_in_policy=True,
        ),
    ] = Field(default="daytona")
    live_enabled: Annotated[
        bool,
        FleetFieldPolicy(
            toml_path="runtime.live_enabled", group="Runtime", label="Live execution", editor="boolean", rank=2
        ),
    ] = Field(
        default=True,
        description="Allow explicitly invoked credentialed provider and benchmark commands",
    )
    data_root: Annotated[
        str,
        FleetFieldPolicy(
            toml_path="storage.data_root",
            group="Storage",
            label="Data root",
            editor="text",
            rank=39,
            required_in_policy=True,
        ),
    ] = Field(default=".fleet_rlm")
    max_upload_bytes: Annotated[
        int,
        FleetFieldPolicy(
            toml_path="storage.max_upload_bytes",
            group="Storage",
            label="Maximum upload bytes",
            editor="number",
            rank=40,
            required_in_policy=True,
        ),
    ] = Field(
        default=10 * 1024 * 1024,
        description="Maximum upload size in bytes",
    )
    max_url_bytes: Annotated[
        int,
        FleetFieldPolicy(
            toml_path="storage.max_url_bytes",
            group="Storage",
            label="Maximum URL source bytes",
            editor="number",
            rank=41,
            required_in_policy=True,
        ),
    ] = Field(
        default=10 * 1024 * 1024,
        description="Maximum public URL source size in bytes",
    )
    max_artifact_bytes: Annotated[
        int,
        FleetFieldPolicy(
            toml_path="storage.max_artifact_bytes",
            group="Storage",
            label="Maximum artifact bytes",
            editor="number",
            rank=42,
            required_in_policy=True,
        ),
    ] = Field(
        default=10 * 1024 * 1024,
        description="Maximum artifact body size in bytes",
    )
    turn_timeout_seconds: Annotated[
        int,
        FleetFieldPolicy(
            toml_path="runtime.turn_timeout_seconds",
            group="Runtime",
            label="Turn timeout seconds",
            editor="number",
            rank=3,
            required_in_policy=True,
        ),
    ] = Field(
        default=1800,
        gt=0,
        description="Turn Timeout in wall-clock seconds for one RLM Turn",
    )
    max_active_daytona_leases: Annotated[
        int,
        FleetFieldPolicy(
            toml_path="runtime.max_active_daytona_leases",
            group="Runtime",
            label="Maximum Daytona leases",
            editor="number",
            rank=4,
            required_in_policy=True,
        ),
    ] = Field(
        default=8,
        gt=0,
        le=8,
        description="Daytona Admission bound for process-wide acquiring or active Interpreter Leases",
    )
    rlm_max_iters: Annotated[
        int,
        FleetFieldPolicy(
            toml_path="rlm.max_iters",
            group="RLM",
            label="Maximum iterations",
            editor="number",
            rank=25,
            required_in_policy=True,
        ),
    ] = Field(default=20, gt=0)
    rlm_max_llm_calls: Annotated[
        int,
        FleetFieldPolicy(
            toml_path="rlm.max_llm_calls",
            group="RLM",
            label="Maximum LLM calls",
            editor="number",
            rank=26,
            required_in_policy=True,
        ),
    ] = Field(default=50, gt=0)
    rlm_max_output_chars: Annotated[
        int,
        FleetFieldPolicy(
            toml_path="rlm.max_output_chars",
            group="RLM",
            label="Maximum output characters",
            editor="number",
            rank=27,
            required_in_policy=True,
        ),
    ] = Field(default=10_000, gt=0)
    rlm_max_execution_output_chars: Annotated[
        int,
        FleetFieldPolicy(
            toml_path="rlm.max_execution_output_chars",
            group="RLM",
            label="Maximum execution output characters",
            editor="number",
            rank=28,
            required_in_policy=True,
        ),
    ] = Field(default=4_000, gt=0)
    rlm_execution_timeout_s: Annotated[
        int,
        FleetFieldPolicy(
            toml_path="rlm.execution_timeout_s",
            group="RLM",
            label="Sandbox execution timeout (seconds)",
            editor="number",
            rank=29,
            required_in_policy=True,
        ),
    ] = Field(default=120, gt=0)
    rlm_recursion_enabled: Annotated[
        bool,
        FleetFieldPolicy(
            toml_path="rlm.recursion_enabled",
            group="RLM",
            label="Enable recursive child RLMs",
            editor="boolean",
            rank=30,
        ),
    ] = False
    rlm_recursion_max_calls: Annotated[
        int,
        FleetFieldPolicy(
            toml_path="rlm.recursion_max_calls", group="RLM", label="Recursive maximum calls", editor="number", rank=31
        ),
    ] = Field(default=4, gt=0)
    rlm_recursion_max_prompt_chars: Annotated[
        int,
        FleetFieldPolicy(
            toml_path="rlm.recursion_max_prompt_chars",
            group="RLM",
            label="Recursive prompt character bound",
            editor="number",
            rank=32,
        ),
    ] = Field(default=50_000, gt=0)
    rlm_recursion_child_max_iters: Annotated[
        int,
        FleetFieldPolicy(
            toml_path="rlm.recursion_child_max_iters",
            group="RLM",
            label="Child maximum iterations",
            editor="number",
            rank=33,
        ),
    ] = Field(default=8, gt=0)
    rlm_recursion_child_max_llm_calls: Annotated[
        int,
        FleetFieldPolicy(
            toml_path="rlm.recursion_child_max_llm_calls",
            group="RLM",
            label="Child maximum LLM calls",
            editor="number",
            rank=34,
        ),
    ] = Field(default=12, gt=0)
    rlm_recursion_child_max_output_chars: Annotated[
        int,
        FleetFieldPolicy(
            toml_path="rlm.recursion_child_max_output_chars",
            group="RLM",
            label="Child maximum output characters",
            editor="number",
            rank=35,
        ),
    ] = Field(default=4_000, gt=0)
    rlm_recursion_max_parallel_children: Annotated[
        int,
        FleetFieldPolicy(
            toml_path="rlm.recursion_max_parallel_children",
            group="RLM",
            label="Maximum parallel child RLMs",
            editor="number",
            rank=36,
        ),
    ] = Field(default=2, gt=0, le=8)
    rlm_autonomous_memory_categories: Annotated[
        tuple[str, ...],
        FleetFieldPolicy(
            toml_path="rlm.autonomous_memory_categories",
            group="RLM",
            label="Autonomous Memory categories",
            editor="string_list",
            rank=37,
        ),
    ] = Field(default=())
    run_heartbeat_seconds: Annotated[
        int,
        FleetFieldPolicy(
            toml_path="runtime.heartbeat_seconds",
            group="Runtime",
            label="Heartbeat seconds",
            editor="number",
            rank=5,
            required_in_policy=True,
        ),
    ] = Field(default=10, gt=0)
    run_stale_after_seconds: Annotated[
        int,
        FleetFieldPolicy(
            toml_path="runtime.stale_after_seconds",
            group="Runtime",
            label="Stale after seconds",
            editor="number",
            rank=6,
            required_in_policy=True,
        ),
    ] = Field(default=60, gt=0)
    rlm_verbose: Annotated[
        bool,
        FleetFieldPolicy(
            toml_path="rlm.verbose",
            group="RLM",
            label="DSPy host verbose logging",
            editor="boolean",
            rank=38,
            required_in_policy=True,
        ),
    ] = True
    log_level: Annotated[
        Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"],
        FleetFieldPolicy(
            toml_path="logging.level",
            group="Logging",
            label="Level",
            editor="single_choice",
            choices=(
                "CRITICAL",
                "ERROR",
                "WARNING",
                "INFO",
                "DEBUG",
            ),
            rank=49,
            required_in_policy=True,
        ),
    ] = "INFO"
    root_llm_api_key_env: Annotated[
        str,
        FleetFieldPolicy(
            toml_path="llm.root.api_key_env",
            group="Root LLM",
            label="Provider API key environment variable",
            editor="text",
            rank=8,
            required_in_policy=True,
        ),
    ] = "FLEET_OPENAI_API_KEY"
    root_llm_base_url: Annotated[
        str | None,
        FleetFieldPolicy(
            toml_path="llm.root.base_url", group="Root LLM", label="Provider base URL", editor="text", rank=9
        ),
    ] = None
    root_llm_max_tokens: Annotated[
        int | None,
        FleetFieldPolicy(
            toml_path="llm.root.max_tokens", group="Root LLM", label="Maximum tokens", editor="number", rank=11
        ),
    ] = Field(default=None, ge=1)
    root_llm_temperature: Annotated[
        float | None,
        FleetFieldPolicy(
            toml_path="llm.root.temperature", group="Root LLM", label="Temperature", editor="number", rank=12
        ),
    ] = None
    root_llm_reasoning_effort: Annotated[
        Literal["none", "low", "medium", "high"] | None,
        FleetFieldPolicy(
            toml_path="llm.root.reasoning_effort",
            group="Root LLM",
            label="Reasoning effort",
            editor="single_choice",
            choices=(
                "none",
                "low",
                "medium",
                "high",
            ),
            rank=15,
        ),
    ] = None
    root_llm_cache: Annotated[
        bool, FleetFieldPolicy(toml_path="llm.root.cache", group="Root LLM", label="Cache", editor="boolean", rank=13)
    ] = True
    root_llm_num_retries: Annotated[
        int,
        FleetFieldPolicy(toml_path="llm.root.num_retries", group="Root LLM", label="Retries", editor="number", rank=14),
    ] = Field(default=3, ge=0)
    sub_llm_api_key_env: Annotated[
        str,
        FleetFieldPolicy(
            toml_path="llm.sub.api_key_env",
            group="Sub LLM",
            label="Provider API key environment variable",
            editor="text",
            rank=17,
            required_in_policy=True,
        ),
    ] = "FLEET_OPENAI_API_KEY"
    sub_llm_base_url: Annotated[
        str | None,
        FleetFieldPolicy(
            toml_path="llm.sub.base_url", group="Sub LLM", label="Provider base URL", editor="text", rank=18
        ),
    ] = None
    sub_llm_max_tokens: Annotated[
        int | None,
        FleetFieldPolicy(
            toml_path="llm.sub.max_tokens", group="Sub LLM", label="Maximum tokens", editor="number", rank=20
        ),
    ] = Field(default=None, ge=1)
    sub_llm_temperature: Annotated[
        float | None,
        FleetFieldPolicy(
            toml_path="llm.sub.temperature", group="Sub LLM", label="Temperature", editor="number", rank=21
        ),
    ] = None
    sub_llm_reasoning_effort: Annotated[
        Literal["none", "low", "medium", "high"] | None,
        FleetFieldPolicy(
            toml_path="llm.sub.reasoning_effort",
            group="Sub LLM",
            label="Reasoning effort",
            editor="single_choice",
            choices=(
                "none",
                "low",
                "medium",
                "high",
            ),
            rank=24,
        ),
    ] = None
    sub_llm_cache: Annotated[
        bool, FleetFieldPolicy(toml_path="llm.sub.cache", group="Sub LLM", label="Cache", editor="boolean", rank=22)
    ] = True
    sub_llm_num_retries: Annotated[
        int,
        FleetFieldPolicy(toml_path="llm.sub.num_retries", group="Sub LLM", label="Retries", editor="number", rank=23),
    ] = Field(default=3, ge=0)
    mlflow_tracing_enabled: Annotated[
        bool,
        FleetFieldPolicy(
            toml_path="mlflow.tracing_enabled", group="MLflow", label="Tracing enabled", editor="boolean", rank=50
        ),
    ] = Field(
        default=False,
        description="Enable Databricks-backed MLflow DSPy autolog (engineering observability)",
    )
    mlflow_experiment_name: Annotated[
        str | None,
        FleetFieldPolicy(
            toml_path="mlflow.experiment_name", group="MLflow", label="Experiment name", editor="text", rank=54
        ),
    ] = Field(
        default=None,
        description="MLflow experiment name when tracing is enabled",
    )
    mlflow_tracking_uri: Annotated[
        str,
        FleetFieldPolicy(toml_path="mlflow.tracking_uri", group="MLflow", label="Tracking URI", editor="text", rank=56),
    ] = Field(
        default="databricks",
        description="MLflow tracking URI selected by the Fleet policy",
    )
    mlflow_expose_trace_id: Annotated[
        bool,
        FleetFieldPolicy(
            toml_path="mlflow.expose_trace_id", group="MLflow", label="Expose trace ID", editor="boolean", rank=57
        ),
    ] = Field(
        default=True,
        description="When tracing is enabled, surface trace ids on Turn SSE metadata",
    )
    mlflow_async_logging: Annotated[
        bool,
        FleetFieldPolicy(
            toml_path="mlflow.async_logging", group="MLflow", label="Async trace logging", editor="boolean", rank=51
        ),
    ] = Field(
        default=True,
        description="Upload MLflow trace data asynchronously when tracing is enabled",
    )
    mlflow_trace_sampling_ratio: Annotated[
        float,
        FleetFieldPolicy(
            toml_path="mlflow.trace_sampling_ratio",
            group="MLflow",
            label="Trace sampling ratio",
            editor="number",
            rank=52,
        ),
    ] = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Fraction of Turn traces to sample for MLflow",
    )
    mlflow_trace_content_max_chars: Annotated[
        int,
        FleetFieldPolicy(
            toml_path="mlflow.trace_content_max_chars",
            group="MLflow",
            label="Trace payload character limit",
            editor="number",
            rank=53,
        ),
    ] = Field(
        default=10_000,
        ge=256,
        le=50_000,
        description="Maximum characters retained in one readable MLflow trace payload field",
    )
    mlflow_trace_catalog: Annotated[
        str | None,
        FleetFieldPolicy(
            toml_path="mlflow.trace_catalog", group="MLflow", label="Trace catalog", editor="text", rank=58
        ),
    ] = Field(default=None)
    mlflow_trace_schema: Annotated[
        str | None,
        FleetFieldPolicy(toml_path="mlflow.trace_schema", group="MLflow", label="Trace schema", editor="text", rank=60),
    ] = Field(default=None)
    mlflow_trace_table_prefix: Annotated[
        str | None,
        FleetFieldPolicy(
            toml_path="mlflow.trace_table_prefix", group="MLflow", label="Trace table prefix", editor="text", rank=62
        ),
    ] = Field(default=None)
    mlflow_tracing_sql_warehouse_id: Annotated[
        str | None,
        FleetFieldPolicy(
            toml_path="mlflow.tracing_sql_warehouse_id",
            group="MLflow",
            label="Tracing SQL warehouse ID",
            editor="text",
            rank=64,
        ),
    ] = Field(default=None)
    posthog_enabled: Annotated[
        bool,
        FleetFieldPolicy(
            toml_path="posthog.enabled", group="PostHog", label="Analytics enabled", editor="boolean", rank=66
        ),
    ] = Field(
        default=False,
        description="Enable PostHog product analytics selected by the Fleet policy",
    )
    posthog_project_token: Annotated[
        SecretStr | None,
        FleetFieldPolicy(
            toml_path=None,
            secret=True,
            doc="Resolved at runtime from the environment named by posthog.project_token_env",
        ),
    ] = Field(
        default=None,
        description="PostHog project token resolved from posthog.project_token_env",
    )
    posthog_host: Annotated[
        str | None,
        FleetFieldPolicy(toml_path="posthog.host", group="PostHog", label="Ingestion host", editor="text", rank=68),
    ] = Field(
        default=None,
        description="PostHog ingestion host selected by the Fleet policy",
    )

    _dotenv_values: dict[str, str] = PrivateAttr(default_factory=dict)
    _active_profile: str | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _validate_run_liveness(self) -> Settings:
        if self.run_stale_after_seconds < self.run_heartbeat_seconds * 3:
            raise ValueError("FLEET_RUN_STALE_AFTER_SECONDS must be at least three times FLEET_RUN_HEARTBEAT_SECONDS")
        return self

    @field_validator("rlm_autonomous_memory_categories")
    @classmethod
    def _validate_autonomous_memory_categories(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if type(value) not in (list, tuple):
            raise ValueError("rlm_autonomous_memory_categories must be a category list")
        from fleet_rlm.files.memory_candidates import normalize_memory_candidate_categories

        try:
            return normalize_memory_candidate_categories(value)
        except ValueError as exc:
            raise ValueError("rlm_autonomous_memory_categories contains an invalid Workspace Memory category") from exc

    @field_validator("llm_base_url", mode="before")
    @classmethod
    def _sanitize_llm_base_url(cls, value: object) -> str | None:
        """
        Normalize an optional LLM service base URL.

        Parameters:
            value (object): Candidate URL value to sanitize.

        Returns:
            str | None: The normalized HTTP(S) URL without trailing slashes, or `None` for empty or invalid values.
        """
        if value is None or value == "":
            return None
        text = str(value).strip().strip("'\"")
        if " #" in text:
            text = text.split(" #", 1)[0].rstrip().strip("'\"")
        if not (text.startswith("http://") or text.startswith("https://")):
            return None
        return text.rstrip("/")

    @field_validator("posthog_host")
    @classmethod
    def _validate_posthog_host(cls, value: str | None) -> str | None:
        """
        Validate and normalize the optional PostHog ingestion host.

        Parameters:
            value (str | None): PostHog ingestion host URL.

        Returns:
            str | None: The normalized URL without a trailing slash, or ``None`` for an empty value.

        Raises:
            ValueError: If the value is not an absolute HTTP or HTTPS URL.
        """
        if value is None or value == "":
            return None
        text = str(value).strip()
        parsed = urllib.parse.urlparse(text)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("posthog_host must be an absolute http(s) URL")
        return text.rstrip("/")

    @field_validator("daytona_snapshot", mode="before")
    @classmethod
    def _sanitize_daytona_snapshot(cls, value: object) -> str | None:
        """
        Normalize and validate a Daytona snapshot name.

        Parameters:
            value (object): Candidate snapshot name.

        Returns:
            str | None: The validated snapshot name, or `None` for empty values.

        Raises:
            ValueError: If the snapshot name is not immutable or does not end with a positive version number.
        """
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return validate_snapshot_name(text)
        except ValueError as exc:
            raise ValueError("FLEET_DAYTONA_SNAPSHOT must be immutable and end in -v<positive integer>") from exc

    def llm_role(self, role: Literal["root", "sub"]) -> LLMRoleSettings:
        """
        Build the configured settings for the requested LLM role.

        Parameters:
            role (Literal["root", "sub"]): The role whose settings to return.

        Returns:
            LLMRoleSettings: The configured Root or Sub role settings.
        """
        prefix = f"{role}_llm"
        return LLMRoleSettings(
            model=self.root_model if role == "root" else self.sub_model,
            api_key_env=getattr(self, f"{prefix}_api_key_env"),
            base_url=getattr(self, f"{prefix}_base_url"),
            max_tokens=getattr(self, f"{prefix}_max_tokens"),
            temperature=getattr(self, f"{prefix}_temperature"),
            reasoning_effort=getattr(self, f"{prefix}_reasoning_effort"),
            cache=getattr(self, f"{prefix}_cache"),
            num_retries=getattr(self, f"{prefix}_num_retries"),
        )

    @property
    def root_lm(self) -> LLMRoleSettings:
        """Resolved Root role settings consumed by model builders and diagnostics."""
        return self.llm_role("root")

    @property
    def sub_lm(self) -> LLMRoleSettings:
        """Resolved Sub role settings consumed by model builders and diagnostics."""
        return self.llm_role("sub")

    @property
    def lm_roles(self) -> LLMRoleBundle:
        """Resolved Root/Sub role bundle; callers no longer chase flat fields."""
        return LLMRoleBundle(root=self.root_lm, sub=self.sub_lm)


@dataclass(frozen=True, slots=True)
class EnvironmentReferenceSpec:
    """A TOML ``*_env`` key resolving one Settings field from the process environment.

    Environment-reference keys are operator-editable policy surface but do not
    bind a ``Settings`` field directly: ``load_runtime_settings`` resolves the
    named variable via the environment/``.env`` seam only.  ``resolves_to``
    names the owning Settings field so inventory generation cannot drift from
    the schema.
    """

    toml_path: str
    group: str
    label: str
    rank: int
    resolves_to: str


# Operator-editable ``*_env`` TOML keys. TOML *paths* are declared once here
# because no Settings field can own them; the *resolved field identity* is
# referenced, never duplicated. Ranks pin positions in the policy inventory.
_ENVIRONMENT_REFERENCE_SPECS: tuple[EnvironmentReferenceSpec, ...] = (
    EnvironmentReferenceSpec(
        toml_path="llm.root.base_url_env",
        group="Root LLM",
        label="Provider base URL environment variable",
        rank=10,
        resolves_to="root_llm_base_url",
    ),
    EnvironmentReferenceSpec(
        toml_path="llm.sub.base_url_env",
        group="Sub LLM",
        label="Provider base URL environment variable",
        rank=19,
        resolves_to="sub_llm_base_url",
    ),
    EnvironmentReferenceSpec(
        toml_path="storage.database_url_env",
        group="Storage",
        label="Database URL environment variable",
        rank=43,
        resolves_to="database_url",
    ),
    EnvironmentReferenceSpec(
        toml_path="daytona.api_key_env",
        group="Daytona",
        label="API key environment variable",
        rank=44,
        resolves_to="daytona_api_key",
    ),
    EnvironmentReferenceSpec(
        toml_path="mlflow.experiment_name_env",
        group="MLflow",
        label="Experiment environment variable",
        rank=55,
        resolves_to="mlflow_experiment_name",
    ),
    EnvironmentReferenceSpec(
        toml_path="mlflow.trace_catalog_env",
        group="MLflow",
        label="Trace catalog environment variable",
        rank=59,
        resolves_to="mlflow_trace_catalog",
    ),
    EnvironmentReferenceSpec(
        toml_path="mlflow.trace_schema_env",
        group="MLflow",
        label="Trace schema environment variable",
        rank=61,
        resolves_to="mlflow_trace_schema",
    ),
    EnvironmentReferenceSpec(
        toml_path="mlflow.trace_table_prefix_env",
        group="MLflow",
        label="Trace table prefix environment variable",
        rank=63,
        resolves_to="mlflow_trace_table_prefix",
    ),
    EnvironmentReferenceSpec(
        toml_path="mlflow.tracing_sql_warehouse_id_env",
        group="MLflow",
        label="Tracing SQL warehouse environment variable",
        rank=65,
        resolves_to="mlflow_tracing_sql_warehouse_id",
    ),
    EnvironmentReferenceSpec(
        toml_path="posthog.project_token_env",
        group="PostHog",
        label="Project token environment variable",
        rank=67,
        resolves_to="posthog_project_token",
    ),
)


@dataclass(frozen=True, slots=True)
class ConfigFieldSpec:
    """Resolved authoritative descriptor for one supported TOML policy key."""

    toml_path: str
    section: str
    key: str
    group: str
    label: str
    editor: EditorKind
    choices: tuple[str, ...]
    rank: int
    settings_field: str | None
    environment_reference_for: str | None
    required_in_policy: bool


@dataclass(frozen=True, slots=True)
class FlattenedPolicy:
    """TOML-derived ``Settings`` constructor input, split by resolution seam.

    ``settings`` carries TOML-bound values keyed by Settings field name.
    ``environment_references`` maps Settings field names to the environment
    variables named by ``*_env`` TOML keys; only the runtime load seam
    resolves those variables into values.
    """

    settings: Mapping[str, Any]
    environment_references: Mapping[str, str]


_EDITOR_KINDS: frozenset[str] = frozenset(getattr(EditorKind, "__args__", ()))
_MISSING: Any = object()


def _field_policies() -> dict[str, FleetFieldPolicy]:
    """Return each Settings field's authoritative policy declaration."""

    policies: dict[str, FleetFieldPolicy] = {}
    for name, field_info in Settings.model_fields.items():
        meta = next((item for item in field_info.metadata if isinstance(item, FleetFieldPolicy)), None)
        if meta is None:
            raise FleetConfigurationError(f"Settings.{name} is missing its authoritative FleetFieldPolicy declaration")
        policies[name] = meta
    return policies


def _lookup_toml(mapping: Mapping[str, Any], toml_path: str) -> Any:
    """Return the value at ``toml_path`` or the ``_MISSING`` sentinel."""

    current: Any = mapping
    for part in toml_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _build_field_specs() -> tuple[ConfigFieldSpec, ...]:
    """Build the canonical policy inventory and prove it cannot drift from ``Settings``.

    Raises:
        FleetConfigurationError: If a Settings field lacks a declaration, a declaration is
            incomplete, paths/ranks collide, environment references resolve unknown fields,
            or the Root/Sub LLM key surfaces diverge.
    """

    policies = _field_policies()
    specs: list[ConfigFieldSpec] = []
    env_targets: set[str] = set()
    for env in _ENVIRONMENT_REFERENCE_SPECS:
        if not env.toml_path.endswith("_env"):
            raise FleetConfigurationError(f"environment reference path must end with _env: {env.toml_path}")
        if env.resolves_to not in policies:
            raise FleetConfigurationError(
                f"environment reference {env.toml_path} resolves unknown Settings field: {env.resolves_to}"
            )
        env_targets.add(env.resolves_to)
        section = env.toml_path.split(".", 1)[0]
        specs.append(
            ConfigFieldSpec(
                toml_path=env.toml_path,
                section=section,
                key=env.toml_path.rsplit(".", 1)[-1],
                group=env.group,
                label=env.label,
                editor="text",
                choices=(),
                rank=env.rank,
                settings_field=None,
                environment_reference_for=env.resolves_to,
                required_in_policy=False,
            )
        )
    for name, meta in policies.items():
        if meta.toml_path is None:
            if meta.group is not None or meta.label is not None or meta.editor is not None or meta.rank is not None:
                raise FleetConfigurationError(f"Settings.{name} declares editor metadata without a TOML mapping")
            if not (meta.secret or meta.compatibility or name in env_targets):
                raise FleetConfigurationError(
                    f"Settings.{name} has no TOML mapping and is neither secret, "
                    "compatibility, nor environment-resolved"
                )
            continue
        if meta.secret:
            raise FleetConfigurationError(f"Settings.{name} is secret and must not be TOML-settable")
        if meta.group is None or meta.label is None or meta.editor is None or meta.rank is None:
            raise FleetConfigurationError(
                f"Settings.{name} TOML mapping requires complete editor metadata (group, label, editor, rank)"
            )
        if meta.editor not in _EDITOR_KINDS:
            raise FleetConfigurationError(f"Settings.{name} declares unsupported editor kind: {meta.editor}")
        expects_choices = meta.editor in ("single_choice", "multi_choice")
        if bool(meta.choices) != expects_choices:
            raise FleetConfigurationError(
                f"Settings.{name} choice metadata does not match its editor kind: {meta.editor}"
            )
        section = meta.toml_path.split(".", 1)[0]
        specs.append(
            ConfigFieldSpec(
                toml_path=meta.toml_path,
                section=section,
                key=meta.toml_path.rsplit(".", 1)[-1],
                group=meta.group,
                label=meta.label,
                editor=meta.editor,
                choices=meta.choices,
                rank=meta.rank,
                settings_field=name,
                environment_reference_for=None,
                required_in_policy=meta.required_in_policy,
            )
        )
    seen_paths: set[str] = set()
    seen_ranks: set[int] = set()
    for spec in specs:
        if spec.toml_path in seen_paths:
            raise FleetConfigurationError(f"duplicate supported TOML path: {spec.toml_path}")
        if spec.rank in seen_ranks:
            raise FleetConfigurationError(f"duplicate policy inventory rank: {spec.rank}")
        seen_paths.add(spec.toml_path)
        seen_ranks.add(spec.rank)
    root_keys = {spec.key for spec in specs if spec.toml_path.startswith("llm.root.")}
    sub_keys = {spec.key for spec in specs if spec.toml_path.startswith("llm.sub.")}
    if root_keys != sub_keys:
        raise FleetConfigurationError(
            "Root/Sub LLM policy keys diverge: "
            f"root-only={sorted(root_keys - sub_keys)} sub-only={sorted(sub_keys - root_keys)}"
        )
    return tuple(sorted(specs, key=lambda spec: spec.rank))


_FIELD_SPECS: tuple[ConfigFieldSpec, ...] = _build_field_specs()


def config_field_specs() -> tuple[ConfigFieldSpec, ...]:
    """Return the authoritative supported-key inventory derived from ``Settings``."""

    return _FIELD_SPECS


def _derive_table_keys(specs: tuple[ConfigFieldSpec, ...]) -> tuple[dict[str, frozenset[str]], frozenset[str]]:
    """Derive the supported TOML key surfaces from the authoritative inventory."""

    tables: dict[str, set[str]] = {}
    role_keys: set[str] = set()
    for spec in specs:
        if spec.section == "llm":
            tables.setdefault("llm", set()).add(spec.toml_path.split(".")[1])
            role_keys.add(spec.key)
        else:
            tables.setdefault(spec.section, set()).add(spec.key)
    return {name: frozenset(keys) for name, keys in tables.items()}, frozenset(role_keys)


_TABLE_KEYS, _ROLE_KEYS = _derive_table_keys(_FIELD_SPECS)

# Non-LLM sections: supported ``*_env`` keys and (direct, env) pairs that may
# not be declared together; both derive from the authoritative inventory.
_ENV_REFERENCE_KEYS: dict[str, tuple[str, ...]] = {
    section: tuple(
        spec.key for spec in _FIELD_SPECS if spec.section == section and spec.environment_reference_for is not None
    )
    for section in dict.fromkeys(spec.section for spec in _FIELD_SPECS if spec.section != "llm")
}
_EXCLUSIVE_ENV_PAIRS: dict[str, tuple[tuple[str, str], ...]] = {
    section: tuple(
        (direct.key, f"{direct.key}_env")
        for direct in _FIELD_SPECS
        if direct.section == section
        and direct.environment_reference_for is None
        and any(
            env.section == section
            and env.environment_reference_for == direct.settings_field
            and env.key == f"{direct.key}_env"
            for env in _FIELD_SPECS
        )
    )
    for section in dict.fromkeys(spec.section for spec in _FIELD_SPECS if spec.section != "llm")
}
_SECRET_RESOLVED_FIELDS: frozenset[str] = frozenset(
    spec.environment_reference_for
    for spec in _FIELD_SPECS
    if spec.environment_reference_for is not None and _field_policies()[spec.environment_reference_for].secret
)


def _require_mapping(value: object, location: str) -> Mapping[str, Any]:
    """
    Require a TOML value to be a mapping.

    Parameters:
        value (object): Value to validate.
        location (str): Configuration path used in the validation error.

    Returns:
        Mapping[str, Any]: The validated mapping.

    Raises:
        FleetConfigurationError: If the value is not a mapping.
    """
    if not isinstance(value, Mapping):
        raise FleetConfigurationError(f"{location} must be a TOML table")
    return cast(Mapping[str, Any], value)


def _validate_policy_table(value: object, location: str, *, allow_partial_llm: bool = False) -> None:
    """
    Validate the structure and environment references in a runtime policy table.

    Parameters:
        value (object): Policy table to validate.
        location (str): Configuration path used in validation errors.
        allow_partial_llm (bool): Whether LLM roles may omit an API key environment reference.

    Raises:
        FleetConfigurationError: If the table contains unknown keys, conflicting values,
            or invalid environment references.
    """
    table = _require_mapping(value, location)
    unknown = set(table).difference(_TABLE_KEYS)
    if unknown:
        raise FleetConfigurationError(f"unknown configuration key(s) at {location}: {', '.join(sorted(unknown))}")
    for name, child in table.items():
        if name != "llm":
            allowed = _TABLE_KEYS[name]
            child_table = _require_mapping(child, f"{location}.{name}")
            extras = set(child_table).difference(allowed)
            if extras:
                raise FleetConfigurationError(
                    f"unknown configuration key(s) at {location}.{name}: {', '.join(sorted(extras))}"
                )
            continue
        llm = _require_mapping(child, f"{location}.llm")
        extras = set(llm).difference(_TABLE_KEYS["llm"])
        if extras:
            raise FleetConfigurationError(
                f"unknown configuration key(s) at {location}.llm: {', '.join(sorted(extras))}"
            )
        for role, role_value in llm.items():
            role_table = _require_mapping(role_value, f"{location}.llm.{role}")
            role_extras = set(role_table).difference(_ROLE_KEYS)
            if role_extras:
                raise FleetConfigurationError(
                    f"unknown configuration key(s) at {location}.llm.{role}: {', '.join(sorted(role_extras))}"
                )
            if "base_url" in role_table and "base_url_env" in role_table:
                raise FleetConfigurationError(f"{location}.llm.{role} cannot define both base_url and base_url_env")
            if "api_key_env" in role_table or not allow_partial_llm:
                _validate_environment_reference(role_table.get("api_key_env"), f"{location}.llm.{role}.api_key_env")
            _validate_optional_environment_reference(
                role_table.get("base_url_env"), f"{location}.llm.{role}.base_url_env"
            )
        continue
    for name, child in table.items():
        if name == "llm":
            continue
        child_table = _require_mapping(child, f"{location}.{name}")
        for direct_key, env_key in _EXCLUSIVE_ENV_PAIRS.get(name, ()):
            if direct_key in child_table and env_key in child_table:
                raise FleetConfigurationError(f"{location}.{name} cannot define both {direct_key} and {env_key}")
        for env_key in _ENV_REFERENCE_KEYS.get(name, ()):
            _validate_optional_environment_reference(child_table.get(env_key), f"{location}.{name}.{env_key}")


def _validate_environment_reference(value: object, location: str) -> str:
    """
    Validate and return an uppercase environment-variable name.

    Parameters:
        value (object): Value to validate as an environment-variable name
        location (str): Configuration location used in validation errors

    Returns:
        str: The validated environment-variable name

    Raises:
        FleetConfigurationError: If the value is not a valid uppercase environment-variable name
    """
    if not isinstance(value, str) or not _ENVIRONMENT_NAME.fullmatch(value):
        raise FleetConfigurationError(f"{location} must name an uppercase environment variable")
    return value


def _validate_optional_environment_reference(value: object, location: str) -> str | None:
    if value is None:
        return None
    return _validate_environment_reference(value, location)


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _flatten_policy(policy: Mapping[str, Any]) -> FlattenedPolicy:
    """Flatten one validated profile table into ``Settings`` constructor input.

    The authoritative field specs own the TOML-path-to-Settings-field mapping;
    absent optional keys stay absent so ``Settings`` defaults apply, and
    ``*_env`` keys become pending environment references resolved only by the
    runtime load seam.

    Parameters:
        policy (Mapping[str, Any]): Validated merged policy table for one profile.

    Returns:
        FlattenedPolicy: Settings values plus pending environment references.

    Raises:
        FleetConfigurationError: If a required policy key is missing.
    """

    settings: dict[str, Any] = {}
    environment_references: dict[str, str] = {}
    missing: list[str] = []
    for spec in _FIELD_SPECS:
        value = _lookup_toml(policy, spec.toml_path)
        if value is _MISSING:
            if spec.required_in_policy:
                missing.append(spec.settings_field or spec.toml_path)
            continue
        if spec.environment_reference_for is not None:
            environment_references[spec.environment_reference_for] = value
        else:
            settings[spec.settings_field or spec.toml_path] = value
    if missing:
        raise FleetConfigurationError(f"selected profile is missing required setting(s): {', '.join(sorted(missing))}")
    return FlattenedPolicy(settings=settings, environment_references=environment_references)


def _unique_environment_names(*values: str | None) -> tuple[str, ...]:
    """Return non-empty environment names in declaration order without duplicates."""
    return tuple(dict.fromkeys(value for value in values if value))


@dataclass(frozen=True, slots=True)
class PolicyDocument:
    """Root ``config/fleet.toml`` document without secret/environment resolution."""

    default_profile: str | None
    defaults: Mapping[str, Any]
    profiles: Mapping[str, Any]


def _policy_document_from_mapping(document: Mapping[str, Any]) -> PolicyDocument:
    """Validate and normalize one loaded Fleet policy root table."""
    root = _require_mapping(document, "root")
    allowed_root = {"config", "defaults", "profiles"}
    unknown = set(root).difference(allowed_root)
    if unknown:
        raise FleetConfigurationError(f"unknown configuration key(s): {', '.join(sorted(unknown))}")
    config = _require_mapping(root.get("config", {}), "config")
    if config.get("schema_version") != 1:
        raise FleetConfigurationError("config.schema_version must be 1")
    unknown_config = set(config).difference({"schema_version", "default_profile"})
    if unknown_config:
        raise FleetConfigurationError(f"unknown configuration key(s) at config: {', '.join(sorted(unknown_config))}")
    default_profile = config.get("default_profile")
    if default_profile is not None and not isinstance(default_profile, str):
        raise FleetConfigurationError("config.default_profile must be a string")
    defaults = _require_mapping(root.get("defaults", {}), "defaults")
    profiles = _require_mapping(root.get("profiles", {}), "profiles")
    if not profiles:
        raise FleetConfigurationError("config.profiles must declare at least one profile")
    _validate_policy_table(defaults, "defaults", allow_partial_llm=True)
    if default_profile is not None and default_profile not in profiles:
        raise FleetConfigurationError(f"configured profile does not exist: {default_profile}")
    return PolicyDocument(default_profile, defaults, profiles)


def _read_policy_document(path: Path) -> PolicyDocument:
    """Read and validate the non-secret Fleet policy document at ``path``."""
    if not path.is_file():
        raise FleetConfigurationError(f"required Fleet configuration file is missing: {path}")
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FleetConfigurationError(f"could not read Fleet configuration: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise FleetConfigurationError(f"invalid Fleet configuration TOML: {exc}") from exc
    return _policy_document_from_mapping(document)


def _profile_contract(
    name: str,
    defaults: Mapping[str, Any],
    selected: object,
) -> ProfileEnvironmentContract:
    """
    Builds the non-secret environment contract for a profile after applying its defaults and overrides.

    Parameters:
        name (str): Profile name used to identify configuration locations.
        defaults (Mapping[str, Any]): Default policy values.
        selected (object): Profile-specific policy values.

    Returns:
        ProfileEnvironmentContract: Validated contract containing the profile's runtime,
            model, and environment-reference settings.
    """
    selected_table = _require_mapping(selected, f"profiles.{name}")
    _validate_policy_table(selected_table, f"profiles.{name}")
    merged = _deep_merge(defaults, selected_table)
    _validate_policy_table(merged, f"profiles.{name}")

    def table(section: str) -> Mapping[str, Any]:
        return _require_mapping(merged.get(section, {}), f"profiles.{name}.{section}")

    runtime = table("runtime")
    llm = table("llm")
    root = _require_mapping(llm.get("root"), f"profiles.{name}.llm.root")
    sub = _require_mapping(llm.get("sub"), f"profiles.{name}.llm.sub")
    daytona = table("daytona")
    storage = table("storage")
    mlflow = table("mlflow")

    def required_text(value: object, location: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise FleetConfigurationError(f"{location} must be a non-blank string")
        return value

    root_api_key_env = _validate_environment_reference(root.get("api_key_env"), f"profiles.{name}.llm.root.api_key_env")
    sub_api_key_env = _validate_environment_reference(sub.get("api_key_env"), f"profiles.{name}.llm.sub.api_key_env")
    root_base_url_env = _validate_optional_environment_reference(
        root.get("base_url_env"), f"profiles.{name}.llm.root.base_url_env"
    )
    sub_base_url_env = _validate_optional_environment_reference(
        sub.get("base_url_env"), f"profiles.{name}.llm.sub.base_url_env"
    )
    daytona_api_key_env = _validate_environment_reference(
        daytona.get("api_key_env"), f"profiles.{name}.daytona.api_key_env"
    )
    database_url_env = _validate_optional_environment_reference(
        storage.get("database_url_env"), f"profiles.{name}.storage.database_url_env"
    )
    mlflow_environment_names = _unique_environment_names(
        *(
            _validate_optional_environment_reference(mlflow.get(f"{field}_env"), f"profiles.{name}.mlflow.{field}_env")
            for field in (
                "experiment_name",
                "trace_catalog",
                "trace_schema",
                "trace_table_prefix",
                "tracing_sql_warehouse_id",
            )
        )
    )
    provider = "OpenAI Chat Completion"
    return ProfileEnvironmentContract(
        name=name,
        runtime_environment=required_text(runtime.get("environment"), f"profiles.{name}.runtime.environment"),
        provider=provider,
        root_model=required_text(root.get("model"), f"profiles.{name}.llm.root.model"),
        sub_model=required_text(sub.get("model"), f"profiles.{name}.llm.sub.model"),
        root_api_key_env=root_api_key_env,
        sub_api_key_env=sub_api_key_env,
        root_base_url_env=root_base_url_env,
        sub_base_url_env=sub_base_url_env,
        root_max_tokens=root.get("max_tokens"),
        sub_max_tokens=sub.get("max_tokens"),
        daytona_api_key_env=daytona_api_key_env,
        database_url_env=database_url_env,
        mlflow_tracing_enabled=bool(mlflow.get("tracing_enabled", False)),
        mlflow_tracking_uri=mlflow.get("tracking_uri"),
        mlflow_environment_names=mlflow_environment_names,
        recursion_enabled=bool(table("rlm").get("recursion_enabled", False)),
    )


def load_profile_environment_contracts(path: Path | None = None) -> tuple[ProfileEnvironmentContract, ...]:
    """Return every profile's provider/environment contract from the TOML policy."""
    document = _read_policy_document(path or _CONFIG_PATH)
    return tuple(_profile_contract(name, document.defaults, selected) for name, selected in document.profiles.items())


def active_profile_contract(path: Path | None = None) -> ProfileEnvironmentContract:
    """Return the contract selected by TOML, never by ambient environment variables."""
    document = _read_policy_document(path or _CONFIG_PATH)
    default_profile = document.default_profile
    if default_profile is None:
        if len(document.profiles) != 1:
            raise FleetConfigurationError("config.default_profile is required when multiple profiles exist")
        default_profile = next(iter(document.profiles))
    return _profile_contract(default_profile, document.defaults, document.profiles[default_profile])


def _resolve_environment_value(name: str | None, dotenv: Mapping[str, str | None]) -> str | None:
    """Resolve one TOML-declared external value; exports win over ``.env``."""
    if name is None:
        return None
    value = os.environ.get(name)
    if value is None:
        value = dotenv.get(name)
    value = (value or "").strip()
    return value or None


def _require_managed_profile_environment_values(
    profile: str,
    flattened: FlattenedPolicy,
    dotenv: Mapping[str, str | None],
) -> None:
    """Fail early when the explicit managed Lakebase/MLflow policy is incomplete."""
    if profile != "daytona-managed":
        return
    # Settings field name -> diagnostic label used when no reference is declared.
    references: tuple[tuple[str, str], ...] = (
        ("database_url", "database_url_env"),
        ("daytona_api_key", "daytona_api_key_env"),
        ("root_llm_api_key_env", "root_llm_api_key_env"),
        ("root_llm_base_url", "root_llm_base_url_env"),
        ("mlflow_experiment_name", "mlflow_experiment_name_env"),
        ("mlflow_trace_catalog", "mlflow_trace_catalog_env"),
        ("mlflow_trace_schema", "mlflow_trace_schema_env"),
        ("mlflow_trace_table_prefix", "mlflow_trace_table_prefix_env"),
        ("mlflow_tracing_sql_warehouse_id", "mlflow_tracing_sql_warehouse_id_env"),
    )
    missing: set[str] = set()
    for field_name, label in references:
        if field_name == "root_llm_api_key_env":
            # The role field stores the environment name directly.
            environment_name: Any = flattened.settings.get(field_name)
        else:
            environment_name = flattened.environment_references.get(field_name)
        if not isinstance(environment_name, str) or not _resolve_environment_value(environment_name, dotenv):
            missing.add(environment_name if isinstance(environment_name, str) else label)
    if missing:
        raise FleetConfigurationError(
            f"selected profile {profile!r} is missing required environment value(s): {', '.join(sorted(missing))}"
        )


def load_runtime_settings() -> Settings:
    """
    Load and validate the runtime settings for the active Fleet profile.

    Returns:
        Settings: Resolved runtime settings, including environment-backed values.

    Raises:
        FleetConfigurationError: If the policy is missing, incomplete, invalid, or unsupported, or
            required environment values are unavailable.
    """
    dotenv = dotenv_values(".env")
    document = _read_policy_document(_CONFIG_PATH)
    defaults = document.defaults
    profiles = document.profiles
    profile = document.default_profile
    if profile is None:
        if len(profiles) == 1:
            profile = next(iter(profiles))
        else:
            raise FleetConfigurationError("config.default_profile is required when multiple profiles exist")
    selected = _require_mapping(profiles[profile], f"profiles.{profile}")
    _validate_policy_table(selected, f"profiles.{profile}")
    flattened = _flatten_policy(_deep_merge(defaults, selected))
    _require_managed_profile_environment_values(profile, flattened, dotenv)

    values: dict[str, Any] = dict(flattened.settings)
    for field_name, environment_name in flattened.environment_references.items():
        resolved = _resolve_environment_value(environment_name, dotenv)
        if field_name in _SECRET_RESOLVED_FIELDS:
            values[field_name] = SecretStr(resolved) if resolved is not None else None
        else:
            values[field_name] = resolved
    settings = Settings(**values)
    settings._dotenv_values = {key: value for key, value in dotenv.items() if value is not None}
    settings._active_profile = profile
    return settings


def require_live_execution() -> Settings:
    """Resolve the selected policy and require its live execution switch.

    This is deliberately separate from command invocation: callers still need
    to invoke a live script explicitly, while this single policy check provides
    the repository-wide fail-closed switch for credentialed commands.
    """
    settings = load_runtime_settings()
    if not settings.live_enabled:
        raise FleetConfigurationError("live execution is disabled by runtime.live_enabled=false")
    return settings


def active_profile(settings: Settings) -> str | None:
    """Return the TOML-selected active profile for the resolved settings."""
    return settings._active_profile


def configure_logging(settings: Settings) -> None:
    """Apply Fleet-owned logger levels without configuring handlers or sinks."""
    level = getattr(logging, settings.log_level)
    logging.getLogger("fleet_rlm").setLevel(level)
    logging.getLogger("dspy").setLevel(level)


def redacted_policy_summary(settings: Settings, *, profile: str) -> str:
    """Return safe operator diagnostics without resolving any secret values."""
    root = settings.root_lm
    sub = settings.sub_lm
    return (
        f"profile={profile} environment={settings.run_environment} "
        f"root_model={root.model} sub_model={sub.model} "
        f"rlm_iters={settings.rlm_max_iters} "
        f"rlm_llm_calls={settings.rlm_max_llm_calls} "
        f"rlm_verbose={settings.rlm_verbose} log_level={settings.log_level} "
        f"volume={settings.volume_name} "
        f"mlflow_tracing={settings.mlflow_tracing_enabled}"
    )
