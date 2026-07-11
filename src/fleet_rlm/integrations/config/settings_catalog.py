"""Declarative catalog for editable runtime settings surfaces."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeSettingDefinition:
    key: str
    category: str
    category_label: str
    category_description: str
    label: str
    description: str
    secret: bool = False
    include_in_default_snapshot: bool = True
    editable: bool = True
    reload_required: bool = False
    placeholder: str | None = None
    default: str | None = None


_CATEGORIES = {
    "llm": ("LLM provider and models", "Planner, delegate, adapter, and provider routing settings used by DSPy."),
    "api_keys": ("API keys and credentials", "Write-only credentials used by providers and optional services."),
    "sandbox_volumes": ("Sandbox and volumes", "Daytona runtime, sandbox execution, and durable volumes."),
    "database": ("Database", "Postgres persistence URLs and database startup behavior."),
}
CATEGORY_METADATA = tuple((key, label, description) for key, (label, description) in _CATEGORIES.items())


def _setting(
    key: str,
    category: str,
    label: str,
    description: str,
    *,
    secret: bool = False,
    reload_required: bool = False,
    placeholder: str | None = None,
    default: str | None = None,
) -> RuntimeSettingDefinition:
    category_label, category_description = _CATEGORIES[category]
    return RuntimeSettingDefinition(
        key=key,
        category=category,
        category_label=category_label,
        category_description=category_description,
        label=label,
        description=description,
        secret=secret,
        reload_required=reload_required,
        placeholder=placeholder,
        default=default,
    )


RUNTIME_SETTING_DEFINITIONS: tuple[RuntimeSettingDefinition, ...] = (
    _setting(
        "DSPY_LM_MODEL",
        "llm",
        "Planner LM model",
        "Model identifier for the planner runtime.",
        reload_required=True,
        placeholder="openai/gpt-4o",
        default="",
    ),
    _setting(
        "DSPY_DELEGATE_LM_MODEL",
        "llm",
        "Delegate LM model",
        "Optional model for recursive delegate turns.",
        reload_required=True,
        placeholder="openai/gpt-4o-mini",
    ),
    _setting(
        "DSPY_DELEGATE_LM_SMALL_MODEL",
        "llm",
        "Delegate small LM model",
        "Optional small model for lightweight delegate tasks.",
        reload_required=True,
        placeholder="openai/gpt-4o-mini",
    ),
    _setting(
        "DSPY_DELEGATE_LM_MAX_TOKENS",
        "llm",
        "Delegate max tokens",
        "Maximum output tokens per delegate response.",
        reload_required=True,
        placeholder="4096",
        default="4096",
    ),
    _setting(
        "DSPY_LM_API_BASE",
        "llm",
        "Provider API base",
        "Optional custom provider API base URL.",
        reload_required=True,
        placeholder="https://api.openai.com/v1",
    ),
    _setting(
        "DSPY_DELEGATE_LM_API_BASE",
        "llm",
        "Delegate API base",
        "Optional delegate provider API base URL.",
        reload_required=True,
        placeholder="https://api.openai.com/v1",
    ),
    _setting(
        "DSPY_LM_MAX_TOKENS",
        "llm",
        "Planner max tokens",
        "Maximum output tokens per planner response.",
        reload_required=True,
        placeholder="2048",
        default="2048",
    ),
    _setting(
        "FLEET_RLM_ACTION_MAX_TOKENS",
        "llm",
        "RLM action max tokens",
        "Maximum output tokens for each RLM action-generation call.",
        reload_required=True,
        placeholder="2048",
        default="2048",
    ),
    _setting(
        "DSPY_PLANNER_LM_TIMEOUT_S",
        "llm",
        "Planner LM request timeout (s)",
        "Per-request planner timeout.",
        reload_required=True,
        placeholder="30",
        default="30",
    ),
    _setting(
        "DSPY_DELEGATE_LM_TIMEOUT_S",
        "llm",
        "Delegate LM request timeout (s)",
        "Per-request delegate timeout.",
        reload_required=True,
        placeholder="120",
        default="120",
    ),
    _setting(
        "DSPY_PLANNER_LM_TEMPERATURE",
        "llm",
        "Planner LM temperature",
        "Optional planner sampling temperature.",
        reload_required=True,
        placeholder="0.0",
        default="0.0",
    ),
    _setting(
        "DSPY_ADAPTER",
        "llm",
        "DSPy adapter",
        "Optional default DSPy adapter.",
        reload_required=True,
        placeholder="chat",
    ),
    _setting(
        "DSPY_ADAPTER_USE_NATIVE_FUNCTION_CALLING",
        "llm",
        "Native function calling",
        "Enable native function calling for the default adapter.",
        reload_required=True,
        placeholder="false",
        default="false",
    ),
    _setting(
        "DSPY_LLM_API_KEY",
        "api_keys",
        "Primary LM API key",
        "Primary provider credential.",
        secret=True,
        reload_required=True,
    ),
    _setting(
        "DSPY_LM_API_KEY",
        "api_keys",
        "Legacy LM API key",
        "Backward-compatible provider credential.",
        secret=True,
        reload_required=True,
    ),
    _setting(
        "DSPY_DELEGATE_LM_API_KEY",
        "api_keys",
        "Delegate LM API key",
        "Optional delegate provider credential.",
        secret=True,
        reload_required=True,
    ),
    _setting("DAYTONA_API_KEY", "api_keys", "Daytona API key", "Credential for Daytona provisioning.", secret=True),
    _setting("POSTHOG_API_KEY", "api_keys", "PostHog API key", "Optional analytics credential.", secret=True),
    _setting(
        "DAYTONA_API_URL",
        "sandbox_volumes",
        "Daytona API URL",
        "Base URL for Daytona.",
        placeholder="http://127.0.0.1:3000",
    ),
    _setting(
        "DAYTONA_TARGET",
        "sandbox_volumes",
        "Daytona target",
        "Execution target for Daytona provisioning.",
        placeholder="local",
    ),
    _setting(
        "VOLUME_NAME",
        "sandbox_volumes",
        "Volume name",
        "Durable Daytona volume name.",
        placeholder="rlm-volume-dspy",
        default="rlm-volume-dspy",
    ),
    _setting(
        "TIMEOUT",
        "sandbox_volumes",
        "Sandbox timeout",
        "Maximum sandbox execution time in seconds.",
        placeholder="900",
        default="900",
    ),
    _setting(
        "INTERPRETER_ASYNC_EXECUTE",
        "sandbox_volumes",
        "Async interpreter execution",
        "Run interpreter calls through the async wrapper.",
        placeholder="true",
        default="true",
    ),
    _setting(
        "DATABASE_URL",
        "database",
        "Runtime database URL",
        "Pooled Postgres runtime URL.",
        secret=True,
        placeholder="postgresql://...",
    ),
    _setting(
        "DATABASE_ADMIN_URL",
        "database",
        "Admin database URL",
        "Direct Postgres admin URL.",
        secret=True,
        placeholder="postgresql://...",
    ),
    _setting(
        "DATABASE_REQUIRED",
        "database",
        "Require database",
        "Require database connectivity at startup.",
        placeholder="false",
        default="false",
    ),
    _setting(
        "DB_ECHO", "database", "SQL echo", "Enable SQLAlchemy SQL echo logging.", placeholder="false", default="false"
    ),
    _setting(
        "DB_VALIDATE_ON_STARTUP",
        "database",
        "Validate database on startup",
        "Ping the database during startup.",
        placeholder="false",
        default="false",
    ),
)

DEFAULT_SETTINGS_KEYS = tuple(item.key for item in RUNTIME_SETTING_DEFINITIONS if item.include_in_default_snapshot)
RUNTIME_SETTINGS_KEYS = tuple(item.key for item in RUNTIME_SETTING_DEFINITIONS if item.editable)
RUNTIME_SETTINGS_ALLOWLIST = frozenset(RUNTIME_SETTINGS_KEYS)
RUNTIME_SETTING_INDEX = {item.key: item for item in RUNTIME_SETTING_DEFINITIONS}
NON_SECRET_KEYS = frozenset(item.key for item in RUNTIME_SETTING_DEFINITIONS if not item.secret)


__all__ = [
    "DEFAULT_SETTINGS_KEYS",
    "CATEGORY_METADATA",
    "NON_SECRET_KEYS",
    "RUNTIME_SETTING_DEFINITIONS",
    "RUNTIME_SETTING_INDEX",
    "RUNTIME_SETTINGS_ALLOWLIST",
    "RUNTIME_SETTINGS_KEYS",
    "RuntimeSettingDefinition",
]
