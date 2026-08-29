"""Public attachment domain types (no host paths)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AttachmentAccess:
    user_id: UUID
    workspace_id: UUID


class AsyncByteSource(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True, slots=True)
class AttachmentUpload:
    filename: str
    content_type: str | None
    source: AsyncByteSource


@dataclass(frozen=True, slots=True)
class AttachmentRun:
    session_id: UUID
    run_id: UUID


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    """Safe metadata returned to API clients."""

    id: UUID
    filename: str
    content_type: str | None
    byte_size: int
    checksum_sha256: str


@dataclass(frozen=True, slots=True)
class StagedAttachment:
    """Logical Sandbox/Volume path for RLM tools (Fleet-controlled only)."""

    attachment_id: UUID
    sandbox_path: str  # e.g. /home/daytona/fleet/sessions/.../attachments/{id}


@dataclass(frozen=True, slots=True)
class PreparedAttachment:
    """Authorized, integrity-checked Attachment metadata ready for one Run."""

    attachment_id: UUID
    filename: str
    content_type: str | None
    byte_size: int
    checksum_sha256: str


@dataclass(frozen=True, slots=True)
class PreparedAttachments:
    refs: tuple[AttachmentRef, ...]
    staged: tuple[StagedAttachment, ...]


class RunAttachmentSink(Protocol):
    async def write_private(self, logical_path: str, data: bytes) -> None: ...

    async def remove_private(self, logical_path: str) -> None: ...
