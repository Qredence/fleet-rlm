"""Authoritative configuration editor catalog; contains no runtime services."""

from dataclasses import dataclass
from typing import Literal

EditorKind = Literal["text", "number", "boolean", "single_choice", "multi_choice", "string_list"]


@dataclass(frozen=True, slots=True)
class FleetFieldPolicy:
    """Authoritative policy declaration for one ``Settings`` field.

    The catalog contains exactly one declaration per ``Settings`` field so the supported
    TOML location, operator grouping, and editor affordances live in one catalog.
    ``_TABLE_KEYS``, policy flattening, and the ``config_policy`` editor inventory
    derive from these declarations instead of mirroring field names by hand.

    ``toml_path=None`` marks fields that TOML cannot set: secrets resolved at
    the runtime seam.  Operator-editable fields must declare complete editor
    metadata plus a unique ``rank`` that pins the policy inventory ordering.
    """

    toml_path: str | None
    group: str | None = None
    label: str | None = None
    editor: EditorKind | None = None
    choices: tuple[str, ...] = ()
    rank: int | None = None
    required_in_policy: bool = False
    secret: bool = False
    doc: str | None = None


FIELD_POLICIES: dict[str, FleetFieldPolicy] = {
    "tavily_api_key": FleetFieldPolicy(toml_path=None, secret=True, doc="Resolved from web.api_key_env"),
    "web_search_enabled": FleetFieldPolicy(
        toml_path="web.search_enabled", group="Web", label="Enable Tavily search", editor="boolean", rank=130
    ),
    "web_search_credits_per_turn": FleetFieldPolicy(
        toml_path="web.search_credits_per_turn", group="Web", label="Search credits per Turn", editor="number", rank=132
    ),
    "app_name": FleetFieldPolicy(
        toml_path="application.name", group="Application", label="Name", editor="text", rank=0, required_in_policy=True
    ),
    "daytona_api_key": FleetFieldPolicy(
        toml_path=None, secret=True, doc="Provider credential resolved at runtime from daytona.api_key_env"
    ),
    "daytona_snapshot": FleetFieldPolicy(
        toml_path=None, doc="Session snapshot resolved at runtime from daytona.snapshot_env"
    ),
    "daytona_child_snapshot": FleetFieldPolicy(
        toml_path=None, doc="Lean SemanticChild snapshot resolved at runtime from daytona.child_snapshot_env"
    ),
    "daytona_org_id": FleetFieldPolicy(
        toml_path=None, doc="Daytona organization ID resolved at runtime from daytona.org_id_env"
    ),
    "root_model": FleetFieldPolicy(
        toml_path="llm.root.model", group="Root LLM", label="Model id", editor="text", rank=7, required_in_policy=True
    ),
    "sub_model": FleetFieldPolicy(
        toml_path="llm.sub.model", group="Sub LLM", label="Model id", editor="text", rank=16, required_in_policy=True
    ),
    "database_url": FleetFieldPolicy(
        toml_path=None, doc="Resolved at runtime from the environment named by storage.database_url_env"
    ),
    "volume_name": FleetFieldPolicy(
        toml_path="daytona.volume_name",
        group="Daytona",
        label="Volume name",
        editor="text",
        rank=47,
        required_in_policy=True,
    ),
    "volume_mount_path": FleetFieldPolicy(
        toml_path="daytona.volume_mount_path",
        group="Daytona",
        label="Volume mount path",
        editor="text",
        rank=48,
        required_in_policy=True,
    ),
    "run_environment": FleetFieldPolicy(
        toml_path="runtime.environment",
        group="Runtime",
        label="Environment",
        editor="single_choice",
        choices=("daytona",),
        rank=1,
        required_in_policy=True,
    ),
    "live_enabled": FleetFieldPolicy(
        toml_path="runtime.live_enabled", group="Runtime", label="Live execution", editor="boolean", rank=2
    ),
    "data_root": FleetFieldPolicy(
        toml_path="storage.data_root",
        group="Storage",
        label="Data root",
        editor="text",
        rank=39,
        required_in_policy=True,
    ),
    "max_upload_bytes": FleetFieldPolicy(
        toml_path="storage.max_upload_bytes",
        group="Storage",
        label="Maximum upload bytes",
        editor="number",
        rank=40,
        required_in_policy=True,
    ),
    "max_artifact_bytes": FleetFieldPolicy(
        toml_path="storage.max_artifact_bytes",
        group="Storage",
        label="Maximum artifact bytes",
        editor="number",
        rank=42,
        required_in_policy=True,
    ),
    "turn_timeout_seconds": FleetFieldPolicy(
        toml_path="runtime.turn_timeout_seconds",
        group="Runtime",
        label="Turn timeout seconds",
        editor="number",
        rank=3,
        required_in_policy=True,
    ),
    "max_active_daytona_leases": FleetFieldPolicy(
        toml_path="runtime.max_active_daytona_leases",
        group="Runtime",
        label="Maximum Daytona leases",
        editor="number",
        rank=4,
        required_in_policy=True,
    ),
    "workspace_io_idle_seconds": FleetFieldPolicy(
        toml_path="runtime.workspace_io_idle_seconds",
        group="Runtime",
        label="Workspace Sandbox idle seconds",
        editor="number",
        rank=121,
    ),
    "workspace_io_acquisition_timeout_seconds": FleetFieldPolicy(
        toml_path="runtime.workspace_io_acquisition_timeout_seconds",
        group="Runtime",
        label="Workspace acquisition timeout seconds",
        editor="number",
        rank=122,
    ),
    "rlm_max_iters": FleetFieldPolicy(
        toml_path="rlm.max_iters",
        group="RLM",
        label="Maximum iterations",
        editor="number",
        rank=25,
        required_in_policy=True,
    ),
    "rlm_max_llm_calls": FleetFieldPolicy(
        toml_path="rlm.max_llm_calls",
        group="RLM",
        label="Maximum LLM calls",
        editor="number",
        rank=26,
        required_in_policy=True,
    ),
    "rlm_max_tool_calls": FleetFieldPolicy(
        toml_path="rlm.max_tool_calls",
        group="RLM",
        label="Maximum Tool calls",
        editor="number",
        rank=74,
        required_in_policy=True,
    ),
    "rlm_max_output_chars": FleetFieldPolicy(
        toml_path="rlm.max_output_chars",
        group="RLM",
        label="Maximum output characters",
        editor="number",
        rank=27,
        required_in_policy=True,
    ),
    "rlm_max_final_output_chars": FleetFieldPolicy(
        toml_path="rlm.max_final_output_chars",
        group="RLM",
        label="Maximum final output characters",
        editor="number",
        rank=77,
        required_in_policy=True,
    ),
    "rlm_max_execution_output_chars": FleetFieldPolicy(
        toml_path="rlm.max_execution_output_chars",
        group="RLM",
        label="Maximum execution output characters",
        editor="number",
        rank=28,
        required_in_policy=True,
    ),
    "rlm_max_execution_output_bytes": FleetFieldPolicy(
        toml_path="rlm.max_execution_output_bytes",
        group="RLM",
        label="Maximum execution output bytes",
        editor="number",
        rank=75,
        required_in_policy=True,
    ),
    "rlm_wrap_up_seconds": FleetFieldPolicy(
        toml_path="rlm.wrap_up_seconds", group="RLM", label="Final-answer reserve (seconds)", editor="number", rank=69
    ),
    "rlm_finalization_attempts": FleetFieldPolicy(
        toml_path="rlm.finalization_attempts",
        group="RLM",
        label="Finalization attempts",
        editor="number",
        rank=76,
        required_in_policy=True,
    ),
    "rlm_recursion_max_calls": FleetFieldPolicy(
        toml_path="rlm.recursion_max_calls", group="RLM", label="Recursive maximum calls", editor="number", rank=31
    ),
    "rlm_recursion_max_prompt_chars": FleetFieldPolicy(
        toml_path="rlm.recursion_max_prompt_chars",
        group="RLM",
        label="Recursive prompt character bound",
        editor="number",
        rank=32,
    ),
    "rlm_recursion_child_max_iters": FleetFieldPolicy(
        toml_path="rlm.recursion_child_max_iters",
        group="RLM",
        label="Child maximum iterations",
        editor="number",
        rank=33,
    ),
    "rlm_recursion_child_max_llm_calls": FleetFieldPolicy(
        toml_path="rlm.recursion_child_max_llm_calls",
        group="RLM",
        label="Child maximum LLM calls",
        editor="number",
        rank=34,
    ),
    "rlm_recursion_child_max_output_chars": FleetFieldPolicy(
        toml_path="rlm.recursion_child_max_output_chars",
        group="RLM",
        label="Child maximum output characters",
        editor="number",
        rank=35,
    ),
    "rlm_recursion_max_parallel_children": FleetFieldPolicy(
        toml_path="rlm.recursion_max_parallel_children",
        group="RLM",
        label="Maximum parallel child RLMs",
        editor="number",
        rank=36,
    ),
    "rlm_autonomous_memory_categories": FleetFieldPolicy(
        toml_path="rlm.autonomous_memory_categories",
        group="RLM",
        label="Autonomous Memory categories",
        editor="string_list",
        rank=37,
    ),
    "run_heartbeat_seconds": FleetFieldPolicy(
        toml_path="runtime.heartbeat_seconds",
        group="Runtime",
        label="Heartbeat seconds",
        editor="number",
        rank=5,
        required_in_policy=True,
    ),
    "run_stale_after_seconds": FleetFieldPolicy(
        toml_path="runtime.stale_after_seconds",
        group="Runtime",
        label="Stale after seconds",
        editor="number",
        rank=6,
        required_in_policy=True,
    ),
    "rlm_verbose": FleetFieldPolicy(
        toml_path="rlm.verbose",
        group="RLM",
        label="DSPy host verbose logging",
        editor="boolean",
        rank=38,
        required_in_policy=True,
    ),
    "log_level": FleetFieldPolicy(
        toml_path="logging.level",
        group="Logging",
        label="Level",
        editor="single_choice",
        choices=("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"),
        rank=49,
        required_in_policy=True,
    ),
    "root_llm_api_key_env": FleetFieldPolicy(
        toml_path="llm.root.api_key_env",
        group="Root LLM",
        label="Provider API key environment variable",
        editor="text",
        rank=8,
        required_in_policy=True,
    ),
    "root_llm_base_url": FleetFieldPolicy(
        toml_path="llm.root.base_url", group="Root LLM", label="Provider base URL", editor="text", rank=9
    ),
    "root_llm_max_tokens": FleetFieldPolicy(
        toml_path="llm.root.max_tokens", group="Root LLM", label="Maximum tokens", editor="number", rank=11
    ),
    "root_llm_timeout_seconds": FleetFieldPolicy(
        toml_path="llm.root.timeout_seconds",
        group="Root LLM",
        label="Provider timeout seconds",
        editor="number",
        rank=70,
    ),
    "root_llm_temperature": FleetFieldPolicy(
        toml_path="llm.root.temperature", group="Root LLM", label="Temperature", editor="number", rank=12
    ),
    "root_llm_reasoning_effort": FleetFieldPolicy(
        toml_path="llm.root.reasoning_effort",
        group="Root LLM",
        label="Reasoning effort",
        editor="single_choice",
        choices=("none", "low", "medium", "high"),
        rank=15,
    ),
    "root_llm_cache": FleetFieldPolicy(
        toml_path="llm.root.cache", group="Root LLM", label="Cache", editor="boolean", rank=13
    ),
    "root_llm_num_retries": FleetFieldPolicy(
        toml_path="llm.root.num_retries", group="Root LLM", label="Retries", editor="number", rank=14
    ),
    "sub_llm_api_key_env": FleetFieldPolicy(
        toml_path="llm.sub.api_key_env",
        group="Sub LLM",
        label="Provider API key environment variable",
        editor="text",
        rank=17,
        required_in_policy=True,
    ),
    "sub_llm_base_url": FleetFieldPolicy(
        toml_path="llm.sub.base_url", group="Sub LLM", label="Provider base URL", editor="text", rank=18
    ),
    "sub_llm_max_tokens": FleetFieldPolicy(
        toml_path="llm.sub.max_tokens", group="Sub LLM", label="Maximum tokens", editor="number", rank=20
    ),
    "sub_llm_timeout_seconds": FleetFieldPolicy(
        toml_path="llm.sub.timeout_seconds", group="Sub LLM", label="Provider timeout seconds", editor="number", rank=71
    ),
    "sub_llm_temperature": FleetFieldPolicy(
        toml_path="llm.sub.temperature", group="Sub LLM", label="Temperature", editor="number", rank=21
    ),
    "sub_llm_reasoning_effort": FleetFieldPolicy(
        toml_path="llm.sub.reasoning_effort",
        group="Sub LLM",
        label="Reasoning effort",
        editor="single_choice",
        choices=("none", "low", "medium", "high"),
        rank=24,
    ),
    "sub_llm_cache": FleetFieldPolicy(
        toml_path="llm.sub.cache", group="Sub LLM", label="Cache", editor="boolean", rank=22
    ),
    "sub_llm_num_retries": FleetFieldPolicy(
        toml_path="llm.sub.num_retries", group="Sub LLM", label="Retries", editor="number", rank=23
    ),
    "mlflow_tracing_enabled": FleetFieldPolicy(
        toml_path="mlflow.tracing_enabled", group="MLflow", label="Tracing enabled", editor="boolean", rank=50
    ),
    "mlflow_experiment_name": FleetFieldPolicy(
        toml_path="mlflow.experiment_name", group="MLflow", label="Experiment name", editor="text", rank=54
    ),
    "mlflow_experiment_purpose": FleetFieldPolicy(
        toml_path="mlflow.experiment_purpose", group="MLflow", label="Experiment purpose", editor="text", rank=117
    ),
    "mlflow_tracking_uri": FleetFieldPolicy(
        toml_path="mlflow.tracking_uri", group="MLflow", label="Tracking URI", editor="text", rank=56
    ),
    "mlflow_expose_trace_id": FleetFieldPolicy(
        toml_path="mlflow.expose_trace_id", group="MLflow", label="Expose trace ID", editor="boolean", rank=57
    ),
    "mlflow_async_logging": FleetFieldPolicy(
        toml_path="mlflow.async_logging", group="MLflow", label="Async trace logging", editor="boolean", rank=51
    ),
    "mlflow_trace_sampling_ratio": FleetFieldPolicy(
        toml_path="mlflow.trace_sampling_ratio", group="MLflow", label="Trace sampling ratio", editor="number", rank=52
    ),
    "mlflow_trace_content_enabled": FleetFieldPolicy(
        toml_path="mlflow.trace_content_enabled",
        group="MLflow",
        label="Capture sanitized trace content",
        editor="boolean",
        rank=110,
    ),
    "mlflow_trace_export_queue_size": FleetFieldPolicy(
        toml_path="mlflow.trace_export_queue_size",
        group="MLflow",
        label="Trace export queue size",
        editor="number",
        rank=111,
    ),
    "mlflow_trace_export_workers": FleetFieldPolicy(
        toml_path="mlflow.trace_export_workers", group="MLflow", label="Trace export workers", editor="number", rank=112
    ),
    "mlflow_trace_export_retry_seconds": FleetFieldPolicy(
        toml_path="mlflow.trace_export_retry_seconds",
        group="MLflow",
        label="Trace export retry limit",
        editor="number",
        rank=113,
    ),
    "mlflow_http_request_timeout_seconds": FleetFieldPolicy(
        toml_path="mlflow.http_request_timeout_seconds",
        group="MLflow",
        label="MLflow HTTP request timeout",
        editor="number",
        rank=115,
    ),
    "mlflow_trace_shutdown_seconds": FleetFieldPolicy(
        toml_path="mlflow.trace_shutdown_seconds",
        group="MLflow",
        label="Trace shutdown wait",
        editor="number",
        rank=114,
    ),
    "mlflow_trace_content_max_chars": FleetFieldPolicy(
        toml_path="mlflow.trace_content_max_chars",
        group="MLflow",
        label="Trace payload character limit",
        editor="number",
        rank=53,
    ),
    "mlflow_trace_catalog": FleetFieldPolicy(
        toml_path="mlflow.trace_catalog", group="MLflow", label="Trace catalog", editor="text", rank=58
    ),
    "mlflow_trace_schema": FleetFieldPolicy(
        toml_path="mlflow.trace_schema", group="MLflow", label="Trace schema", editor="text", rank=60
    ),
    "mlflow_trace_table_prefix": FleetFieldPolicy(
        toml_path="mlflow.trace_table_prefix", group="MLflow", label="Trace table prefix", editor="text", rank=62
    ),
    "mlflow_tracing_sql_warehouse_id": FleetFieldPolicy(
        toml_path="mlflow.tracing_sql_warehouse_id",
        group="MLflow",
        label="Tracing SQL warehouse ID",
        editor="text",
        rank=64,
    ),
    "posthog_enabled": FleetFieldPolicy(
        toml_path="posthog.enabled", group="PostHog", label="Analytics enabled", editor="boolean", rank=66
    ),
    "posthog_project_token": FleetFieldPolicy(
        toml_path=None, secret=True, doc="Resolved at runtime from the environment named by posthog.project_token_env"
    ),
    "posthog_host": FleetFieldPolicy(
        toml_path="posthog.host", group="PostHog", label="Ingestion host", editor="text", rank=68
    ),
    "capture_enabled": FleetFieldPolicy(
        toml_path="capture.enabled", group="Capture", label="Turn capture enabled", editor="boolean", rank=118
    ),
    "capture_retention_days": FleetFieldPolicy(
        toml_path="capture.retention_days", group="Capture", label="Capture retention (days)", editor="number", rank=119
    ),
    "capture_max_captures": FleetFieldPolicy(
        toml_path="capture.max_captures", group="Capture", label="Retained capture files", editor="number", rank=120
    ),
}


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


ENVIRONMENT_REFERENCE_SPECS: tuple[EnvironmentReferenceSpec, ...] = (
    EnvironmentReferenceSpec(
        toml_path="web.api_key_env",
        group="Web",
        label="Tavily API key environment variable",
        rank=131,
        resolves_to="tavily_api_key",
    ),
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
        toml_path="daytona.snapshot_env",
        group="Daytona",
        label="Session snapshot environment variable",
        rank=45,
        resolves_to="daytona_snapshot",
    ),
    EnvironmentReferenceSpec(
        toml_path="daytona.child_snapshot_env",
        group="Daytona",
        label="SemanticChild snapshot environment variable",
        rank=116,
        resolves_to="daytona_child_snapshot",
    ),
    EnvironmentReferenceSpec(
        toml_path="daytona.org_id_env",
        group="Daytona",
        label="Organization ID environment variable",
        rank=46,
        resolves_to="daytona_org_id",
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
