"""Configuration models for fleet-rlm using Pydantic.

This module defines the type-safe configuration schema for the agent, interpreter,
and memory systems. It is designed to be used with Hydra for hierarchical
configuration management (YAML -> Dict -> Pydantic).
"""

from typing import Literal

from pydantic import BaseModel, Field

from .analytics.config import MlflowConfig, PostHogConfig


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
        description="Root path for archival memory in the Modal Volume.",
    )


class InterpreterConfig(BaseModel):
    """Configuration for the Modal Interpreter sandbox."""

    image: str = Field(
        default="python:3.13-slim-bookworm",
        description="Base Docker image for the sandbox.",
    )
    volume_name: str | None = Field(
        default=None,
        description="Name of the Modal Volume to mount (e.g., 'agent-volume').",
    )
    timeout: int = Field(
        default=900,
        description="Maximum execution time for the sandbox in seconds.",
    )
    secrets: list[str] = Field(
        default_factory=list,
        description="List of Modal Secret names to inject into the sandbox.",
    )
    async_execute: bool = Field(
        default=True,
        description="Whether async interpreter calls should run execute() via a non-blocking async wrapper.",
    )


class AgentConfig(BaseModel):
    """Configuration for the RLMReActChatAgent."""

    max_iters: int = Field(
        default=60,
        description="Maximum number of ReAct loop iterations per turn.",
    )
    model: str = Field(
        default="openai/gemini/gemini-3.1-pro-preview",
        description="LLM model identifier to use. Must include LiteLLM provider prefix e.g. 'openai/model-name'.",
    )
    temperature: float = Field(
        default=1.0,
        description="LLM sampling temperature.",
    )
    delegate_model: str | None = Field(
        default=None,
        description=(
            "Optional cheaper model identifier used for delegate/sub-agent turns. "
            "When unset, delegates use the parent planner model."
        ),
    )
    delegate_max_tokens: int = Field(
        default=64000,
        description="Maximum token budget for delegate model calls.",
    )
    rlm_max_iterations: int = Field(
        default=30,
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
        description="Maximum LLM calls per task.",
    )
    max_output_chars: int = Field(
        default=100000,
        description="Maximum output characters.",
    )
    delegate_max_calls_per_turn: int = Field(
        default=8,
        description="Maximum number of delegate sub-agent spawns in a single turn.",
    )
    delegate_result_truncation_chars: int = Field(
        default=8000,
        description="Maximum delegate response size before truncating for safety.",
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

    agent: AgentConfig = Field(default_factory=AgentConfig)
    interpreter: InterpreterConfig = Field(default_factory=InterpreterConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    rlm_settings: RlmSettings = Field(default_factory=RlmSettings)
    analytics: AnalyticsConfig = Field(default_factory=AnalyticsConfig)
