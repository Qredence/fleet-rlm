"""Strict DTOs at Fleet's model-visible DSPy input boundary."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from fleet_rlm.files.memory_models import WORKSPACE_MEMORY_INJECTION_TAIL_BYTES


class FleetInputModel(BaseModel):
    """Immutable, closed DTO shared only by the RLM input boundary."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )


class TurnPreviewInput(FleetInputModel):
    ordinal: int = Field(ge=1)
    role: Literal["user", "assistant"]
    preview: str = Field(max_length=320)


class WorkspaceCapabilityInput(FleetInputModel):
    available: bool
    root: Literal["."]
    instructions: str


class WorkspaceMemoryInput(FleetInputModel):
    """Bounded untrusted Workspace Memory tail injected at Turn start.

    ``tail`` holds the newest curated memory/MEMORIES.md records (``workspace_memory
    tail``); it is operator/user-managed context, never authoritative evidence.
    """

    tail: str = Field(min_length=1, max_length=WORKSPACE_MEMORY_INJECTION_TAIL_BYTES)


class SessionContextInput(FleetInputModel):
    session_id: UUID
    checkpoint_version: int = Field(ge=0)
    message_count: int = Field(ge=0)
    recent: tuple[TurnPreviewInput, ...] = Field(max_length=6)
    workspace: WorkspaceCapabilityInput
    workspace_memory: WorkspaceMemoryInput | None = None


class SkillCardInput(FleetInputModel):
    id: UUID
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=512)
    scope: Literal["system"]
    version: str = Field(min_length=1, max_length=64)
    trust: Literal["system"]
    affordances: tuple[str, ...] = Field(max_length=8)
    resources_available: bool


class AttachmentInput(FleetInputModel):
    id: UUID
    filename: str = Field(min_length=1, max_length=255)
    content_type: str | None = None
    byte_size: int = Field(ge=0)
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


__all__ = [
    "AttachmentInput",
    "FleetInputModel",
    "SessionContextInput",
    "SkillCardInput",
    "TurnPreviewInput",
    "WorkspaceCapabilityInput",
    "WorkspaceMemoryInput",
]
