"""Public attachment domain types (no host paths)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


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
