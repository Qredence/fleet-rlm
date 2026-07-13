"""Committed Artifact read and candidate policy contracts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest


@dataclass
class _Catalog:
    stored: object

    async def get(self, *, access: object, artifact_id: UUID) -> object:
        return self.stored


@dataclass
class _Blobs:
    data: bytes

    async def read(self, workspace_id: UUID, logical_path: str) -> bytes:
        return self.data


@pytest.mark.asyncio
async def test_artifact_reader_returns_only_integrity_checked_committed_content() -> None:
    from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactRef
    from fleet_rlm.artifacts.reader import ArtifactReader, StoredArtifact

    access = ArtifactAccess(user_id=uuid4(), workspace_id=uuid4())
    ref = ArtifactRef(
        id=uuid4(),
        session_id=uuid4(),
        run_id=uuid4(),
        kind="text",
        title="report",
        media_type="text/plain",
        byte_size=3,
        checksum_sha256="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    )
    reader = ArtifactReader(
        catalog=_Catalog(StoredArtifact(ref=ref, storage_ref="private/artifact")),
        blobs=_Blobs(b"abc"),
    )

    assert await reader.metadata(access, ref.id) == ref
    content = await reader.content(access, ref.id)
    assert content.metadata == ref
    assert content.data == b"abc"
    assert "private/artifact" not in repr(content)


@pytest.mark.asyncio
async def test_artifact_reader_collapses_corrupt_or_missing_bytes_to_not_found() -> None:
    from fleet_rlm.artifacts.errors import ArtifactNotFoundError
    from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactRef
    from fleet_rlm.artifacts.reader import ArtifactReader, StoredArtifact

    ref = ArtifactRef(uuid4(), uuid4(), uuid4(), "text", None, "text/plain", 3, "a" * 64)
    reader = ArtifactReader(
        catalog=_Catalog(StoredArtifact(ref=ref, storage_ref="private/artifact")),
        blobs=_Blobs(b"wrong"),
    )

    with pytest.raises(ArtifactNotFoundError):
        await reader.content(ArtifactAccess(user_id=uuid4(), workspace_id=uuid4()), ref.id)


def test_artifact_promotion_validates_the_complete_owned_candidate_batch() -> None:
    from fleet_rlm.artifacts.errors import ArtifactValidationError
    from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactCandidate
    from fleet_rlm.artifacts.promotion import ArtifactPromotion

    access = ArtifactAccess(user_id=uuid4(), workspace_id=uuid4())
    session_id, run_id = uuid4(), uuid4()
    candidate = ArtifactCandidate(
        id=uuid4(),
        user_id=access.user_id,
        workspace_id=access.workspace_id,
        session_id=session_id,
        run_id=run_id,
        kind="text",
        title=None,
        media_type="text/plain",
        byte_size=3,
        checksum_sha256="ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        staging_path="runs/input/candidate.txt",
        durable_path="artifacts/output.txt",
    )
    policy = ArtifactPromotion(max_bytes=8)

    assert policy.validate((candidate,), access=access, session_id=session_id, run_id=run_id) == (candidate,)
    with pytest.raises(ArtifactValidationError):
        policy.validate((candidate, candidate), access=access, session_id=session_id, run_id=run_id)
