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
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, SecretStr, field_validator, model_validator

from fleet_rlm.config import catalog
from fleet_rlm.config.catalog import FIELD_POLICIES, EditorKind, FleetFieldPolicy
from fleet_rlm.snapshot_contract import validate_snapshot_name

_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


class FleetConfigurationError(ValueError):
    """Raised when the required Fleet runtime policy is invalid."""


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
        """
        Reject unsupported configuration field names.

        Parameters:
            data (Mapping[Any, Any]): Configuration data whose keys are checked.

        Raises:
            FleetConfigurationError: If the data contains fields not defined by the settings model.
        """
        unknown = sorted(key for key in data if key not in cls.model_fields)
        if unknown:
            raise FleetConfigurationError(f"unsupported Settings field(s): {', '.join(map(str, unknown))}")

    app_name: str = Field(default="fleet-rlm")
    daytona_api_key: SecretStr | None = Field(default=None)
    daytona_snapshot: str | None = Field(default=None)
    daytona_child_snapshot: str | None = Field(default=None)
    daytona_org_id: str | None = Field(default=None)
    root_model: str = Field(
        default="openai/gpt-4o-mini",
        description="Root model id sent through the OpenAI-compatible Chat Completion provider",
    )
    sub_model: str = Field(
        default="openai/gpt-4o-mini",
        description="Sub model id sent through the OpenAI-compatible Chat Completion provider",
    )
    database_url: str | None = Field(
        default=None,
        description="Async SQLAlchemy URL (e.g. sqlite+aiosqlite:///:memory: or postgresql+asyncpg://...)",
    )
    volume_name: str = Field(
        default="fleet-volume",
        description="Daytona Volume name for workspace durable files",
    )
    volume_mount_path: str = Field(
        default="/home/daytona/fleet",
        description="Absolute Sandbox mount path for the workspace Volume",
    )
    run_environment: Literal["daytona"] = Field(default="daytona")
    live_enabled: bool = Field(
        default=True,
        description="Allow explicitly invoked credentialed provider and benchmark commands",
    )
    data_root: str = Field(default=".fleet_rlm")
    max_upload_bytes: int = Field(
        default=10 * 1024 * 1024,
        description="Maximum upload size in bytes",
    )
    max_artifact_bytes: int = Field(
        default=10 * 1024 * 1024,
        description="Maximum artifact body size in bytes",
    )
    turn_timeout_seconds: int = Field(
        default=1800,
        gt=0,
        description="Turn Timeout in wall-clock seconds for one RLM Turn",
    )
    max_active_daytona_leases: int = Field(
        default=8,
        ge=2,
        le=8,
        description="Daytona lease limit with one slot reserved for short-lived host I/O",
    )
    workspace_io_idle_seconds: int = Field(
        default=300, gt=0, description="Idle retention of the single warm Workspace I/O Sandbox"
    )
    workspace_io_acquisition_timeout_seconds: int = Field(
        default=120,
        gt=0,
        description="Bound on Workspace I/O queueing, admission and Sandbox preparation (incl. a stopped start)",
    )
    rlm_max_iters: int = Field(default=20, gt=0)
    rlm_max_llm_calls: int = Field(default=50, gt=0)
    rlm_max_tool_calls: int = Field(default=256, gt=0)
    rlm_max_output_chars: int = Field(default=10_000, gt=0)
    rlm_max_final_output_chars: int = Field(default=10_000, gt=0)
    rlm_max_execution_output_chars: int = Field(default=4_000, gt=0)
    rlm_max_execution_output_bytes: int = Field(default=2_000_000, gt=0)
    rlm_wrap_up_seconds: int = Field(default=300, gt=0)
    rlm_finalization_attempts: int = Field(default=2, ge=0)
    rlm_recursion_max_calls: int = Field(default=4, gt=0)
    rlm_recursion_max_prompt_chars: int = Field(default=50_000, gt=0)
    rlm_recursion_child_max_iters: int = Field(default=8, gt=0)
    rlm_recursion_child_max_llm_calls: int = Field(default=12, gt=0)
    rlm_recursion_child_max_output_chars: int = Field(default=4_000, gt=0)
    rlm_recursion_max_parallel_children: int = Field(default=2, gt=0, le=8)
    rlm_autonomous_memory_categories: tuple[str, ...] = Field(default=())
    run_heartbeat_seconds: int = Field(default=10, gt=0)
    run_stale_after_seconds: int = Field(default=60, gt=0)
    rlm_verbose: bool = True
    log_level: Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"] = "INFO"
    root_llm_api_key_env: str = "FLEET_OPENAI_API_KEY"
    root_llm_base_url: str | None = None
    root_llm_max_tokens: int | None = Field(default=None, ge=1)
    root_llm_timeout_seconds: int | None = Field(default=300, gt=0)
    root_llm_temperature: float | None = None
    root_llm_reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    root_llm_cache: bool = True
    root_llm_num_retries: int = Field(default=3, ge=0)
    sub_llm_api_key_env: str = "FLEET_OPENAI_API_KEY"
    sub_llm_base_url: str | None = None
    sub_llm_max_tokens: int | None = Field(default=None, ge=1)
    sub_llm_timeout_seconds: int | None = Field(default=90, gt=0)
    sub_llm_temperature: float | None = None
    sub_llm_reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    sub_llm_cache: bool = True
    sub_llm_num_retries: int = Field(default=3, ge=0)
    mlflow_tracing_enabled: bool = Field(
        default=False,
        description="Enable Databricks-backed MLflow DSPy autolog (engineering observability)",
    )
    mlflow_experiment_name: str | None = Field(
        default=None,
        description="MLflow experiment name when tracing is enabled",
    )
    mlflow_experiment_purpose: str | None = Field(
        default=None,
        description=(
            "Optional purpose recorded as the fleet.experiment.purpose tag on the "
            "configured MLflow experiment (e.g. runtime, evaluation, optimization). "
            "A conflicting recorded purpose fails configuration."
        ),
    )
    mlflow_tracking_uri: str = Field(
        default="databricks",
        description="MLflow tracking URI selected by the Fleet policy",
    )
    mlflow_expose_trace_id: bool = Field(
        default=True,
        description="When tracing is enabled, surface trace ids on Turn SSE metadata",
    )
    mlflow_async_logging: bool = Field(
        default=True,
        description="Upload MLflow trace data asynchronously when tracing is enabled",
    )
    mlflow_trace_sampling_ratio: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Fraction of Turn traces to sample for MLflow",
    )
    mlflow_trace_content_enabled: bool = Field(
        default=True, description="Permit bounded sanitized content in authorized engineering traces"
    )
    mlflow_trace_export_queue_size: int = Field(default=128, ge=1, le=10000)
    mlflow_trace_export_workers: int = Field(default=2, ge=1, le=16)
    mlflow_trace_export_retry_seconds: int = Field(default=10, ge=1, le=120)
    mlflow_http_request_timeout_seconds: int = Field(default=10, ge=1, le=600)
    mlflow_trace_shutdown_seconds: float = Field(default=5.0, gt=0, le=60)
    mlflow_trace_content_max_chars: int = Field(
        default=10_000,
        ge=256,
        le=50_000,
        description="Maximum characters retained in one readable MLflow trace payload field",
    )
    mlflow_trace_catalog: str | None = Field(default=None)
    mlflow_trace_schema: str | None = Field(default=None)
    mlflow_trace_table_prefix: str | None = Field(default=None)
    mlflow_tracing_sql_warehouse_id: str | None = Field(default=None)
    posthog_enabled: bool = Field(
        default=False,
        description="Enable PostHog product analytics selected by the Fleet policy",
    )
    posthog_project_token: SecretStr | None = Field(
        default=None,
        description="PostHog project token resolved from posthog.project_token_env",
    )
    posthog_host: str | None = Field(
        default=None,
        description="PostHog ingestion host selected by the Fleet policy",
    )
    capture_enabled: bool = Field(
        default=True,
        description="Retain one durable local Runtime Event capture file per Run",
    )
    capture_retention_days: int = Field(default=14, ge=1)
    capture_max_captures: int = Field(default=500, ge=1)
    web_search_enabled: bool = Field(default=True, description="Offer optional host-key Tavily public search")
    tavily_api_key: SecretStr | None = Field(
        default=None, description="Tavily credential resolved from web.api_key_env"
    )
    web_search_credits_per_turn: int = Field(default=62, ge=0, le=10000)

    _dotenv_values: dict[str, str] = PrivateAttr(default_factory=dict)

    @model_validator(mode="after")
    def _validate_run_liveness(self) -> Settings:
        if self.run_stale_after_seconds < self.run_heartbeat_seconds * 3:
            raise ValueError("FLEET_RUN_STALE_AFTER_SECONDS must be at least three times FLEET_RUN_HEARTBEAT_SECONDS")
        if self.rlm_wrap_up_seconds >= self.turn_timeout_seconds:
            raise ValueError("rlm_wrap_up_seconds must be less than turn_timeout_seconds")
        if self.rlm_recursion_max_parallel_children > self.rlm_recursion_max_calls:
            raise ValueError("rlm_recursion_max_parallel_children must not exceed rlm_recursion_max_calls")
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

    @field_validator("daytona_snapshot", "daytona_child_snapshot", mode="before")
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
            raise ValueError("Daytona snapshots must be immutable and end in -v<positive integer>") from exc

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

    missing = Settings.model_fields.keys() - FIELD_POLICIES.keys()
    extra = FIELD_POLICIES.keys() - Settings.model_fields.keys()
    if missing or extra:
        raise FleetConfigurationError(
            f"configuration catalog differs from Settings: missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return dict(FIELD_POLICIES)


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
    for env in catalog.ENVIRONMENT_REFERENCE_SPECS:
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
            if not (meta.secret or name in env_targets):
                raise FleetConfigurationError(
                    f"Settings.{name} has no TOML mapping and is neither secret nor environment-resolved"
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
