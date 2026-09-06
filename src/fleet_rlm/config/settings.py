"""Typed process settings for the Fleet RLM backend.

The authoritative ``Settings`` schema and the schema-derived policy inventory.
No clients, engines, LMs, or network access are constructed at import time.
Secrets use ``SecretStr`` so public dumps never expose plaintext values.
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, SecretStr, field_validator, model_validator

from fleet_rlm.snapshot_contract import validate_snapshot_name

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


class LLMRoleSettings(BaseModel):
    """Non-secret settings for one explicit Root or Sub Model role."""

    model_config = ConfigDict(extra="forbid")

    model: str
    api_key_env: str
    base_url: str | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    timeout_seconds: int | None = Field(default=None, gt=0)
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

    runtime_variant: Annotated[
        Literal["legacy"],
        FleetFieldPolicy(
            toml_path="runtime.variant",
            group="Runtime",
            label="Runtime variant",
            editor="single_choice",
            choices=("legacy",),
            rank=72,
        ),
    ] = Field(default="legacy", description="Implemented execution architecture; independent of provider environment")
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
    rlm_wrap_up_seconds: Annotated[
        int,
        FleetFieldPolicy(
            toml_path="rlm.wrap_up_seconds",
            group="RLM",
            label="Final-answer reserve (seconds)",
            editor="number",
            rank=69,
        ),
    ] = Field(default=300, gt=0)
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
    root_llm_timeout_seconds: Annotated[
        int | None,
        FleetFieldPolicy(
            toml_path="llm.root.timeout_seconds",
            group="Root LLM",
            label="Provider timeout seconds",
            editor="number",
            rank=70,
        ),
    ] = Field(default=300, gt=0)
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
    sub_llm_timeout_seconds: Annotated[
        int | None,
        FleetFieldPolicy(
            toml_path="llm.sub.timeout_seconds",
            group="Sub LLM",
            label="Provider timeout seconds",
            editor="number",
            rank=71,
        ),
    ] = Field(default=90, gt=0)
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
        if self.rlm_wrap_up_seconds >= self.turn_timeout_seconds:
            raise ValueError("rlm_wrap_up_seconds must be less than turn_timeout_seconds")
        return self

    @field_validator("rlm_autonomous_memory_categories")
    @classmethod
    def _validate_autonomous_memory_categories(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if type(value) not in (list, tuple):
            raise ValueError("rlm_autonomous_memory_categories must be a category list")
        from fleet_rlm.workspace.memory import normalize_memory_candidate_categories

        try:
            return normalize_memory_candidate_categories(value)
        except ValueError as exc:
            raise ValueError("rlm_autonomous_memory_categories contains an invalid Workspace Memory category") from exc

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
            timeout_seconds=getattr(self, f"{prefix}_timeout_seconds"),
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


_EDITOR_KINDS: frozenset[str] = frozenset(getattr(EditorKind, "__args__", ()))


def _field_policies() -> dict[str, FleetFieldPolicy]:
    """Return each Settings field's authoritative policy declaration."""

    policies: dict[str, FleetFieldPolicy] = {}
    for name, field_info in Settings.model_fields.items():
        meta = next((item for item in field_info.metadata if isinstance(item, FleetFieldPolicy)), None)
        if meta is None:
            raise FleetConfigurationError(f"Settings.{name} is missing its authoritative FleetFieldPolicy declaration")
        policies[name] = meta
    return policies


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
