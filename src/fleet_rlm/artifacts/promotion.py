"""Pure policy for private Artifact Candidates entering Turn Commit."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol
from uuid import UUID

from fleet_rlm.artifacts.errors import ArtifactValidationError
from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactCandidate, ArtifactRef


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
        del access, session_id, run_id
        ids: set[UUID] = set()
        staging_paths: set[str] = set()
        durable_paths: set[str] = set()
        for candidate in candidates:
            if candidate.id in ids:
                raise ArtifactValidationError("Artifact Candidate identities must be unique")
            if candidate.staging_path in staging_paths or candidate.durable_path in durable_paths:
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
            staging_paths.add(candidate.staging_path)
            durable_paths.add(candidate.durable_path)
        return candidates

    @staticmethod
    def _validate_path(value: str) -> None:
        if not value or ".." in PurePosixPath(value).parts or "\\" in value or "\x00" in value:
            raise ArtifactValidationError("Artifact Candidate location is invalid")
