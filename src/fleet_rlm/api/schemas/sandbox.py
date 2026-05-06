"""Pydantic request/response schemas for the FastAPI server."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SandboxListItem(BaseModel):
    """Single sandbox entry returned by the sandbox list endpoint."""

    id: str = Field(description="Sandbox identifier.")
    name: str = Field(description="Sandbox name.")
    state: str = Field(description="Sandbox state (e.g. started, stopped, archived).")
    created_at: str | None = Field(default=None, description="ISO-8601 creation timestamp when available.")
    volume_name: str | None = Field(
        default=None,
        description="Name of the persistent volume attached to the sandbox.",
    )
    labels: dict[str, str] = Field(default_factory=dict, description="Custom labels attached to the sandbox.")
    cpu: int | None = Field(default=None, description="Allocated CPU cores.")
    memory: int | None = Field(default=None, description="Allocated memory in GiB.")
    disk: int | None = Field(default=None, description="Allocated disk in GiB.")


class SandboxDetailResponse(BaseModel):
    """Detailed response for a single sandbox."""

    id: str = Field(description="Sandbox identifier.")
    name: str = Field(description="Sandbox name.")
    state: str = Field(description="Sandbox state (e.g. started, stopped, archived).")
    created_at: str | None = Field(default=None, description="ISO-8601 creation timestamp when available.")
    volume_name: str | None = Field(
        default=None,
        description="Name of the persistent volume attached to the sandbox.",
    )
    labels: dict[str, str] = Field(default_factory=dict, description="Custom labels attached to the sandbox.")
    cpu: int | None = Field(default=None, description="Allocated CPU cores.")
    memory: int | None = Field(default=None, description="Allocated memory in GiB.")
    disk: int | None = Field(default=None, description="Allocated disk in GiB.")
    env_vars: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables configured for the sandbox.",
    )
    image: str | None = Field(default=None, description="Base image or declarative image used by the sandbox.")
    snapshot: str | None = Field(default=None, description="Snapshot name used to create the sandbox.")
    language: str | None = Field(default=None, description="Programming language of the sandbox.")
    auto_stop_interval: int | None = Field(
        default=None,
        description="Minutes of inactivity before auto-stopping.",
    )
    auto_archive_interval: int | None = Field(
        default=None,
        description="Minutes after stop before archiving to cold storage.",
    )
    auto_delete_interval: int | None = Field(
        default=None,
        description="Minutes after archive before permanent deletion.",
    )
    ephemeral: bool | None = Field(default=None, description="Whether the sandbox is ephemeral.")
    network_block_all: bool | None = Field(default=None, description="Whether all outbound network is blocked.")
    network_allow_list: str | None = Field(default=None, description="Comma-separated list of allowed domains.")
    volumes: list[dict[str, Any]] = Field(default_factory=list, description="Detailed volume mounts.")


class SandboxListResponse(BaseModel):
    """Response for the sandbox list endpoint."""

    items: list[SandboxListItem] = Field(default_factory=list, description="Available sandboxes.")
    total: int = Field(description="Total number of sandboxes.")
    page: int = Field(default=1, description="Current page number.")
    total_pages: int = Field(default=1, description="Total number of pages.")


class SandboxArchiveResponse(BaseModel):
    """Result payload after archiving a sandbox."""

    ok: bool = Field(
        default=True,
        description="Whether the sandbox was archived successfully.",
    )


class RunStepItem(BaseModel):
    """Single execution step for a run."""

    id: str = Field(description="Durable step identifier.")
    step_index: int = Field(description="Step position within the run.")
    step_type: str = Field(description="Step type (e.g. tool_call, reasoning).")
    tool_name: str | None = Field(default=None, description="Tool name when applicable.")
    tokens_in: int | None = Field(default=None, description="Input token count.")
    tokens_out: int | None = Field(default=None, description="Output token count.")
    latency_ms: int | None = Field(default=None, description="Step latency in milliseconds.")
    created_at: str = Field(description="ISO-8601 creation timestamp.")


class RunStepListResponse(BaseModel):
    """Paginated execution step list for a run."""

    items: list[RunStepItem] = Field(description="Step list items.")
    total: int = Field(description="Total steps in run.")
    offset: int = Field(description="Current pagination offset.")
    limit: int = Field(description="Current page size.")
    has_more: bool = Field(description="Whether more steps exist beyond this page.")
