"""Committed Artifact read and candidate policy contracts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest


@dataclass
class _Catalog:
    stored: object

    async def get(self, *, access: object, artifact_id: UUID) -> object:
        del access, artifact_id
        return self.stored


@dataclass
class _Blobs:
    data: bytes

    async def read_bytes(self, workspace_id: UUID, logical_path: str) -> bytes:
        del workspace_id, logical_path
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


def test_artifact_promotion_rejects_cross_kind_location_collisions() -> None:
    from dataclasses import replace

    from fleet_rlm.artifacts.errors import ArtifactValidationError
    from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactCandidate
    from fleet_rlm.artifacts.promotion import ArtifactPromotion

    access = ArtifactAccess(user_id=uuid4(), workspace_id=uuid4())
    session_id, run_id = uuid4(), uuid4()
    first = ArtifactCandidate(
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
        staging_path="runs/input/first.txt",
        durable_path="artifacts/first.txt",
    )
    second = replace(
        first,
        id=uuid4(),
        staging_path=first.durable_path,
        durable_path="artifacts/second.txt",
    )

    with pytest.raises(ArtifactValidationError, match="locations must be unique"):
        ArtifactPromotion(max_bytes=8).validate(
            (first, second),
            access=access,
            session_id=session_id,
            run_id=run_id,
        )


@pytest.mark.parametrize("path", ["./runs/input/candidate.txt", "runs//input/candidate.txt"])
def test_artifact_promotion_rejects_noncanonical_locations(path: str) -> None:
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
        staging_path=path,
        durable_path="artifacts/output.txt",
    )

    with pytest.raises(ArtifactValidationError, match="location is invalid"):
        ArtifactPromotion(max_bytes=8).validate(
            (candidate,),
            access=access,
            session_id=session_id,
            run_id=run_id,
        )


@pytest.mark.parametrize("field", ["user_id", "workspace_id", "session_id", "run_id"])
def test_artifact_promotion_rejects_candidate_owned_by_another_identity(field: str) -> None:
    from fleet_rlm.artifacts.errors import ArtifactValidationError
    from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactCandidate
    from fleet_rlm.artifacts.promotion import ArtifactPromotion

    access = ArtifactAccess(user_id=uuid4(), workspace_id=uuid4())
    session_id, run_id = uuid4(), uuid4()
    kwargs = dict(
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
    kwargs[field] = uuid4()
    candidate = ArtifactCandidate(**kwargs)
    policy = ArtifactPromotion(max_bytes=8)

    with pytest.raises(ArtifactValidationError, match="ownership is invalid"):
        policy.validate((candidate,), access=access, session_id=session_id, run_id=run_id)


@pytest.mark.parametrize(
    "path",
    ["../outside.txt", "runs/../../escape", "back\\slash.txt", "nul\x00.bin"],
    ids=["dotdot-prefix", "nested-dotdot", "backslash", "nul-byte"],
)
@pytest.mark.parametrize("location", ["staging_path", "durable_path"])
def test_artifact_promotion_rejects_traversal_in_candidate_locations(path: str, location: str) -> None:
    from fleet_rlm.artifacts.errors import ArtifactValidationError
    from fleet_rlm.artifacts.models import ArtifactAccess, ArtifactCandidate
    from fleet_rlm.artifacts.promotion import ArtifactPromotion

    access = ArtifactAccess(user_id=uuid4(), workspace_id=uuid4())
    session_id, run_id = uuid4(), uuid4()
    kwargs = dict(
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
    kwargs[location] = path
    candidate = ArtifactCandidate(**kwargs)
    policy = ArtifactPromotion(max_bytes=8)

    with pytest.raises(ArtifactValidationError, match="location is invalid"):
        policy.validate((candidate,), access=access, session_id=session_id, run_id=run_id)
