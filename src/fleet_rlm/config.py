"""Configuration models for fleet-rlm using Pydantic.

This module defines the type-safe configuration schema for the agent, interpreter,
and memory systems. It is designed to be used with Hydra for hierarchical
configuration management (YAML -> Dict -> Pydantic).
"""

from typing import Optional
from pydantic import BaseModel, Field


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
        default="python:3.11-slim-bookworm",
        description="Base Docker image for the sandbox.",
    )
    volume_name: Optional[str] = Field(
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


class AgentConfig(BaseModel):
    """Configuration for the RLMReActChatAgent."""

    max_iters: int = Field(
        default=10,
        description="Maximum number of ReAct loop iterations per turn.",
    )
    model: str = Field(
        default="gpt-4-turbo-preview",
        description="LLM model identifier to use.",
    )
    temperature: float = Field(
        default=0.0,
        description="LLM sampling temperature.",
    )
    rlm_max_iterations: int = Field(
        default=30,
        description="Maximum total RLM iterations across delegation.",
    )


class AppConfig(BaseModel):
    """Root configuration for the fleet-rlm application."""

    agent: AgentConfig = Field(default_factory=AgentConfig)
    interpreter: InterpreterConfig = Field(default_factory=InterpreterConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
