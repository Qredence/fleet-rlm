"""Configuration models for fleet-rlm using Pydantic.

This module defines the type-safe configuration schema for the agent, interpreter,
and memory systems. It is designed to be used with Hydra for hierarchical
configuration management (YAML -> Dict -> Pydantic).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from fleet_rlm.integrations.observability.config import MlflowConfig, PostHogConfig


class MemoryConfig(BaseModel):
    """Configuration for agent memory systems."""

    core_memory_limits: dict[str, int] = Field(
        default_factory=lambda: {
            "persona": 2000,
            "human": 2000,
            "scratchpad": 1000,
        },
        description="Character limits for each core memory block.",
    )
    archival_path: str = Field(
        default="/data/memory",
        description="Root path for archival memory in the persistent volume.",
    )


class InterpreterConfig(BaseModel):
    """Configuration for the interpreter sandbox."""

    image: str = Field(
        default="python:3.13-slim-bookworm",
        description="Base Docker image for the sandbox.",
    )
    volume_name: str | None = Field(
        default=None,
        description="Name of the persistent volume to mount (e.g., 'rlm-volume-dspy').",
    )
    timeout: int = Field(
        default=900,
        description="Maximum execution time for the sandbox in seconds.",
    )
    secrets: list[str] = Field(
        default_factory=list,
        description="List of secret names to inject into the sandbox.",
    )
    async_execute: bool = Field(
        default=True,
        description="Whether async interpreter calls should run execute() via a non-blocking async wrapper.",
    )


class LlmConfig(BaseModel):
    """Configuration for language-model provider and model selection."""

    model: str = Field(
        default="",
        description="Planner LLM model identifier. Empty means no model configured; resolvers return None and log a warning.",
    )
    delegate_model: str | None = Field(
        default=None,
        description="Optional model identifier used for delegate/sub-agent turns.",
    )
    delegate_small_model: str | None = Field(
        default=None,
        description="Optional small delegate model identifier.",
    )
    max_tokens: int = Field(
        default=64000,
        description="Maximum output tokens per planner model response.",
    )
    delegate_max_tokens: int = Field(
        default=64000,
        description="Maximum output tokens per delegate model response.",
    )
    api_base: str | None = Field(
        default=None,
        description="Optional LiteLLM-compatible provider API base URL.",
    )
    adapter: str | None = Field(
        default=None,
        description="Optional default DSPy adapter.",
    )
    adapter_use_native_function_calling: bool = Field(
        default=False,
        description="Enable native function calling on the default DSPy adapter.",
    )
    max_iters: int = Field(
        default=60,
        description="Maximum number of ReAct loop iterations per turn.",
    )
    temperature: float = Field(
        default=1.0,
        description="LLM sampling temperature.",
    )
    rlm_max_iterations: int = Field(
        default=15,
        description="Maximum total RLM iterations across delegation.",
    )
    guardrail_mode: Literal["off", "warn", "strict"] = Field(
        default="off",
        description="Guardrail behavior for assistant responses.",
    )
    min_substantive_chars: int = Field(
        default=20,
        description="Minimum response length considered substantive for warning-level guardrails.",
    )


class ApiKeysConfig(BaseModel):
    """Configuration for runtime credentials."""

    llm_api_key: str | None = Field(
        default=None,
        description="Primary provider API key for planner and fallback delegate LM calls.",
    )
    alternate_lm_api_key: str | None = Field(
        default=None,
        description="Backward-compatible LM provider API key.",
    )
    delegate_lm_api_key: str | None = Field(
        default=None,
        description="Optional provider key dedicated to delegate model calls.",
    )
    daytona_api_key: str | None = Field(
        default=None,
        description="API key used for Daytona provisioning.",
    )
    posthog_api_key: str | None = Field(
        default=None,
        description="Optional PostHog API key.",
    )


class SandboxConfig(BaseModel):
    """Configuration for the Daytona sandbox runtime."""

    provider: Literal["daytona"] = Field(
        default="daytona",
        description="Sandbox provider used by the public runtime.",
    )
    image: str = Field(
        default="python:3.13-slim-bookworm",
        description="Base Docker image for the sandbox.",
    )
    timeout: int = Field(
        default=900,
        description="Maximum execution time for the sandbox in seconds.",
    )
    secret_name: str = Field(
        default="LITELLM",
        description="Secret name injected into the sandbox.",
    )
    daytona_api_url: str | None = Field(
        default=None,
        description="Base URL for the Daytona API.",
    )
    daytona_target: str | None = Field(
        default=None,
        description="Daytona target used for provisioning.",
    )
    async_execute: bool = Field(
        default=True,
        description="Whether interpreter calls should use the async wrapper.",
    )


class VolumesConfig(BaseModel):
    """Configuration for durable runtime volumes."""

    name: str | None = Field(
        default=None,
        description="Name of the persistent volume to mount.",
    )


class DatabaseConfig(BaseModel):
    """Configuration for Postgres persistence."""

    url: str | None = Field(
        default=None,
        description="Pooled runtime database URL.",
    )
    admin_url: str | None = Field(
        default=None,
        description="Direct database URL for schema and admin tasks.",
    )
    required: bool = Field(
        default=False,
        description="Require database connectivity during server startup.",
    )
    echo: bool = Field(
        default=False,
        description="Enable SQLAlchemy echo logging.",
    )
    validate_on_startup: bool = Field(
        default=False,
        description="Validate database connectivity during server startup.",
    )


class AgentConfig(BaseModel):
    """Configuration for the agent.

    LM settings (model, delegate_model, delegate_max_tokens, api_key,
    api_base, etc.) live exclusively on :class:`LlmConfig`. This class
    retains only agent-behavior fields that are not LM settings.
    """

    max_iters: int = Field(
        default=60,
        description="Maximum number of ReAct loop iterations per turn.",
    )
    temperature: float = Field(
        default=1.0,
        description="LLM sampling temperature.",
    )
    rlm_max_iterations: int = Field(
        default=15,
        description="Maximum total RLM iterations across delegation.",
    )
    guardrail_mode: Literal["off", "warn", "strict"] = Field(
        default="off",
        description="Guardrail behavior for assistant responses.",
    )
    min_substantive_chars: int = Field(
        default=20,
        description="Minimum response length considered substantive for warning-level guardrails.",
    )


class RlmSettings(BaseModel):
    """RLM execution settings."""

    max_depth: int = Field(
        default=2,
        description="Maximum recursion depth for RLM subagents.",
    )
    max_iters: int = Field(
        default=60,
        description="Maximum iterations for ReAct agent.",
    )
    deep_max_iters: int = Field(
        default=60,
        description="Escalated iteration budget for deep-analysis turns.",
    )
    enable_adaptive_iters: bool = Field(
        default=True,
        description="Enable adaptive turn budgets based on intent and tool errors.",
    )
    max_iterations: int = Field(
        default=60,
        description="Maximum iterations for RLM code execution.",
    )
    max_llm_calls: int = Field(
        default=50,
        description="Maximum semantic sub-LM calls per RLM task.",
    )
    max_output_chars: int = Field(
        default=5000,
        description="Maximum REPL output characters exposed back to the RLM per step.",
    )
    action_max_tokens: int = Field(
        default=2048,
        description="Maximum output tokens for each RLM action-generation call.",
    )
    action_timeout: int = Field(
        default=90,
        description="Maximum seconds for each RLM action-generation call before timeout.",
    )
    url_document_max_iterations: int = Field(
        default=12,
        description="Maximum iterations for URL-document RLM analysis.",
    )
    url_document_max_llm_calls: int = Field(
        default=30,
        description="Maximum semantic sub-LM calls for URL-document RLM analysis.",
    )
    delegate_max_calls_per_turn: int = Field(
        default=8,
        description="Maximum number of delegate sub-agent spawns in a single turn.",
    )
    delegate_result_truncation_chars: int = Field(
        default=8000,
        description="Maximum delegate response size before truncating for safety.",
    )
    delegate_execution_timeout: int = Field(
        default=300,
        description="Maximum seconds for an isolated delegate child execution before it is treated as unavailable. URL-fetch delegates may legitimately exceed 120s due to network I/O and multi-pass analysis.",
    )
    delegate_max_iterations: int = Field(
        default=8,
        description="Maximum REPL iterations for child RLM delegates. Lower values prevent context bloat on simple tasks.",
    )
    delegate_adapter: str = Field(
        default="json",
        description="DSPy adapter for child RLM delegates. 'json' avoids ChatAdapter parse failures with models that produce REPL-style output.",
    )
    daytona_broker_health_timeout: float = Field(
        default=20.0,
        description="Maximum seconds to wait for the Daytona tool broker health check.",
    )
    daytona_broker_tool_call_timeout: float = Field(
        default=180.0,
        description="Maximum seconds the in-sandbox broker waits for a host tool-call response before 504.",
    )
    daytona_broker_start_retries: int = Field(
        default=1,
        description="Number of Daytona tool broker restart attempts before failing delegate execution.",
    )
    child_isolation_mode: Literal["auto", "context"] = Field(
        default="auto",
        description=(
            "Recursive RLM child isolation policy. 'auto' creates isolated child "
            "sandboxes; 'context' keeps context-mode same-sandbox fresh-context execution."
        ),
    )
    child_fork_fallback: Literal["clean", "fail"] = Field(
        default="clean",
        description=(
            "Fallback policy when no-volume child sandbox fork fails. 'clean' retries "
            "with a clean child sandbox; 'fail' returns the fork failure."
        ),
    )
    stdout_summary_threshold: int = Field(
        default=10000,
        description="Threshold for stdout summarization.",
    )
    stdout_summary_prefix_len: int = Field(
        default=200,
        description="Prefix length in summaries.",
    )
    verbose: bool = Field(
        default=True,
        description="Enable verbose logging.",
    )


class AnalyticsConfig(BaseModel):
    """Configuration for runtime analytics integrations."""

    posthog: PostHogConfig = Field(default_factory=PostHogConfig)
    mlflow: MlflowConfig = Field(default_factory=MlflowConfig)


class AppConfig(BaseModel):
    """Root configuration for the fleet-rlm application."""

    llm: LlmConfig = Field(default_factory=LlmConfig)
    api_keys: ApiKeysConfig = Field(default_factory=ApiKeysConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    volumes: VolumesConfig = Field(default_factory=VolumesConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    interpreter: InterpreterConfig = Field(default_factory=InterpreterConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    rlm_settings: RlmSettings = Field(default_factory=RlmSettings)
    analytics: AnalyticsConfig = Field(default_factory=AnalyticsConfig)

    @model_validator(mode="after")
    def _sync_interpreter_sections(self) -> AppConfig:
        """Sync interpreter/sandbox/volumes sections.

        LM fields are no longer duplicated between ``LlmConfig`` and
        ``AgentConfig`` (``LlmConfig`` is the single source), so the former
        cross-section LM sync is gone. Only the interpreter/sandbox/volumes
        bidirectional sync remains.
        """
        fields_set = set(self.model_fields_set)

        if "interpreter" not in fields_set:
            self.interpreter = InterpreterConfig(
                image=self.sandbox.image,
                volume_name=self.volumes.name,
                timeout=self.sandbox.timeout,
                secrets=[self.sandbox.secret_name] if self.sandbox.secret_name else [],
                async_execute=self.sandbox.async_execute,
            )
        elif "sandbox" in fields_set or "volumes" in fields_set:
            self.interpreter = InterpreterConfig(
                image=self.sandbox.image if "sandbox" in fields_set else self.interpreter.image,
                volume_name=self.volumes.name if "volumes" in fields_set else self.interpreter.volume_name,
                timeout=self.sandbox.timeout if "sandbox" in fields_set else self.interpreter.timeout,
                secrets=(
                    [self.sandbox.secret_name]
                    if "sandbox" in fields_set and self.sandbox.secret_name
                    else self.interpreter.secrets
                ),
                async_execute=self.sandbox.async_execute if "sandbox" in fields_set else self.interpreter.async_execute,
            )
            if "sandbox" not in fields_set:
                self.sandbox = SandboxConfig(
                    image=self.interpreter.image,
                    timeout=self.interpreter.timeout,
                    secret_name=self.interpreter.secrets[0] if self.interpreter.secrets else "LITELLM",
                    async_execute=self.interpreter.async_execute,
                )
            if "volumes" not in fields_set:
                self.volumes = VolumesConfig(name=self.interpreter.volume_name)
        else:
            self.sandbox = SandboxConfig(
                image=self.interpreter.image,
                timeout=self.interpreter.timeout,
                secret_name=self.interpreter.secrets[0] if self.interpreter.secrets else "LITELLM",
                async_execute=self.interpreter.async_execute,
            )
            self.volumes = VolumesConfig(name=self.interpreter.volume_name)

        return self
