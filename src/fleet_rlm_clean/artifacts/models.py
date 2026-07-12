"""Public artifact domain types (no host paths)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

ArtifactKind = Literal["text", "markdown", "json"]

KIND_MEDIA_TYPES: dict[ArtifactKind, str] = {
    "text": "text/plain",
    "markdown": "text/markdown",
    "json": "application/json",
}

KIND_EXTENSIONS: dict[ArtifactKind, str] = {
    "text": ".txt",
    "markdown": ".md",
    "json": ".json",
}


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """Safe metadata returned to API clients and turn context."""

    id: UUID
    session_id: UUID
    run_id: UUID
    kind: ArtifactKind
    title: str | None
    media_type: str
    byte_size: int
    checksum_sha256: str
