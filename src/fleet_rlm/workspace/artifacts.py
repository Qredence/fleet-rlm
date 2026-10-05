"""Durable generated artifacts domain models, safety validation, promotion, and reader."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal, Protocol, cast, get_args
from uuid import UUID

# --- Errors ---


class ArtifactError(RuntimeError):
    """Base artifact error."""


class ArtifactNotFoundError(ArtifactError):
    """Missing or unauthorized artifact (do not distinguish for clients)."""


class ArtifactValidationError(ArtifactError):
    """Rejected kind, size, title, or content."""


class ArtifactStorageError(ArtifactError):
    """Committed Artifact catalog or byte storage is unavailable."""


# --- Models ---

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
class ArtifactAccess:
    user_id: UUID
    workspace_id: UUID


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


@dataclass(frozen=True, slots=True)
class ArtifactCandidate:
    """Private Run output that becomes an Artifact only through Turn Commit."""

    id: UUID
    user_id: UUID
    workspace_id: UUID
    session_id: UUID
    run_id: UUID
    kind: ArtifactKind
    title: str | None
    media_type: str
    byte_size: int
    checksum_sha256: str
    staging_path: str
    durable_path: str


@dataclass(frozen=True, slots=True)
class ArtifactContent:
    metadata: ArtifactRef
    data: bytes


@dataclass(frozen=True, slots=True)
class CompletedRun:
    """Run identity whose private result snapshot is allowed to remain."""

    session_id: UUID
    run_id: UUID


# --- Safety Validation ---

DEFAULT_MAX_BYTES = 10 * 1024 * 1024
_SAFE_TITLE = re.compile(r"^[A-Za-z0-9._ -]{1,255}$")
_ALLOWED_KINDS = frozenset(get_args(ArtifactKind))


def parse_kind(kind: str) -> ArtifactKind:
    raw = (kind or "").strip().lower()
    if raw not in _ALLOWED_KINDS:
        raise ArtifactValidationError(f"unsupported artifact kind; expected one of {sorted(_ALLOWED_KINDS)}")
    return cast(ArtifactKind, raw)


def media_type_for(kind: ArtifactKind) -> str:
    return KIND_MEDIA_TYPES[kind]


def sanitize_title(title: str | None) -> str | None:
    if title is None:
        return None
    raw = title.strip()
    if not raw:
        return None
    if "/" in raw or "\\" in raw or ".." in raw:
        raise ArtifactValidationError("invalid title")
    if not _SAFE_TITLE.match(raw):
        raise ArtifactValidationError("invalid title")
    return raw


def validate_content_size(size: int, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
    if size < 0:
        raise ArtifactValidationError("negative size")
    if size == 0:
        raise ArtifactValidationError("empty content")
    if size > max_bytes:
        raise ArtifactValidationError(f"artifact exceeds max size of {max_bytes} bytes")


def encode_content(kind: ArtifactKind, content: str) -> bytes:
    """Validate textual content for kind and return UTF-8 bytes."""
    if not isinstance(content, str):
        raise ArtifactValidationError("content must be a string")
    text = content
    if kind == "json":
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            raise ArtifactValidationError("content is not valid JSON") from exc
    return text.encode("utf-8")


# --- Promotion ---


@dataclass(frozen=True, slots=True)
class PromotedArtifact:
    """Private publication metadata passed into the atomic state transaction."""

    ref: ArtifactRef
    storage_ref: str


class RunArtifactSink(Protocol):
    """Private bounded byte access scoped to one acquired Run environment."""

    async def read(self, location: str, *, max_bytes: int) -> bytes: ...

    async def write(self, location: str, data: bytes) -> None: ...

    async def remove(self, location: str) -> None: ...


class ArtifactPromotion:
    """Validate one complete candidate batch before durable byte writes."""

    def __init__(self, *, max_bytes: int) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._max_bytes = max_bytes

    def validate(
        self,
        candidates: tuple[ArtifactCandidate, ...],
        *,
        access: ArtifactAccess,
        session_id: UUID,
        run_id: UUID,
    ) -> tuple[ArtifactCandidate, ...]:
        ids: set[UUID] = set()
        locations: set[str] = set()
        for candidate in candidates:
            if (
                candidate.user_id != access.user_id
                or candidate.workspace_id != access.workspace_id
                or candidate.session_id != session_id
                or candidate.run_id != run_id
            ):
                raise ArtifactValidationError("Artifact Candidate ownership is invalid")
            if candidate.id in ids:
                raise ArtifactValidationError("Artifact Candidate identities must be unique")
            if candidate.staging_path in locations or candidate.durable_path in locations:
                raise ArtifactValidationError("Artifact Candidate locations must be unique")
            if candidate.staging_path == candidate.durable_path:
                raise ArtifactValidationError("Artifact staging and durable locations must differ")
            self._validate_path(candidate.staging_path)
            self._validate_path(candidate.durable_path)
            if not 1 <= candidate.byte_size <= self._max_bytes:
                raise ArtifactValidationError("Artifact Candidate size is invalid")
            checksum = candidate.checksum_sha256.lower()
            if len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum):
                raise ArtifactValidationError("Artifact Candidate checksum is invalid")
            ids.add(candidate.id)
            locations.update((candidate.staging_path, candidate.durable_path))
        return candidates

    @staticmethod
    def _validate_path(value: str) -> None:
        path = PurePosixPath(value)
        if not value or not path.parts or str(path) != value or ".." in path.parts or "\\" in value or "\x00" in value:
            raise ArtifactValidationError("Artifact Candidate location is invalid")


# --- Reader ---


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    """Private catalog value containing an opaque committed byte reference."""

    ref: ArtifactRef
    storage_ref: str


class ArtifactCatalog(Protocol):
    async def get(self, *, access: ArtifactAccess, artifact_id: UUID) -> StoredArtifact: ...


class ArtifactBlobGateway(Protocol):
    async def read_bytes(self, workspace_id: UUID, logical_path: str) -> bytes: ...


class ArtifactReader:
    """Read committed metadata and content without exposing storage topology."""

    def __init__(self, *, catalog: ArtifactCatalog, blobs: ArtifactBlobGateway) -> None:
        self._catalog = catalog
        self._blobs = blobs

    async def _stored(self, access: ArtifactAccess, artifact_id: UUID) -> StoredArtifact:
        try:
            return await self._catalog.get(access=access, artifact_id=artifact_id)
        except ArtifactError:
            raise
        except Exception as exc:
            raise ArtifactStorageError("Artifact storage is unavailable") from exc

    async def metadata(self, access: ArtifactAccess, artifact_id: UUID) -> ArtifactRef:
        return (await self._stored(access, artifact_id)).ref

    async def content(
        self, access: ArtifactAccess, artifact_id: UUID, *, max_bytes: int | None = None
    ) -> ArtifactContent:
        """Read verified bytes, optionally rejecting oversized content before fetch."""
        if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 1):
            raise ArtifactValidationError("Artifact byte allowance must be a positive integer")
        stored = await self._stored(access, artifact_id)
        if stored.ref.media_type != KIND_MEDIA_TYPES.get(stored.ref.kind):
            raise ArtifactValidationError("Artifact content is not a supported text type")
        if max_bytes is not None and stored.ref.byte_size > max_bytes:
            raise ArtifactValidationError("Artifact exceeds the selected input byte allowance")
        try:
            data = await self._blobs.read_bytes(access.workspace_id, stored.storage_ref)
        except ArtifactError:
            raise
        except (FileNotFoundError, KeyError) as exc:
            raise ArtifactNotFoundError("Artifact not found") from exc
        except Exception as exc:
            raise ArtifactStorageError("Artifact storage is unavailable") from exc
        if len(data) != stored.ref.byte_size or hashlib.sha256(data).hexdigest() != stored.ref.checksum_sha256:
            raise ArtifactNotFoundError("Artifact not found")
        return ArtifactContent(metadata=stored.ref, data=data)


__all__ = [
    "DEFAULT_MAX_BYTES",
    "KIND_EXTENSIONS",
    "KIND_MEDIA_TYPES",
    "ArtifactAccess",
    "ArtifactBlobGateway",
    "ArtifactCandidate",
    "ArtifactCatalog",
    "ArtifactContent",
    "ArtifactError",
    "ArtifactKind",
    "ArtifactNotFoundError",
    "ArtifactPromotion",
    "ArtifactReader",
    "ArtifactRef",
    "ArtifactStorageError",
    "ArtifactValidationError",
    "CompletedRun",
    "PromotedArtifact",
    "RunArtifactSink",
    "StoredArtifact",
    "encode_content",
    "media_type_for",
    "parse_kind",
    "sanitize_title",
    "validate_content_size",
]
