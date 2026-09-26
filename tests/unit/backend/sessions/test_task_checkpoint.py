from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from fleet_rlm.api.schemas import CreateTurnRequest
from fleet_rlm.paths import VolumePaths
from fleet_rlm.sessions.models import TurnInput
from fleet_rlm.sessions.task import (
    SessionTaskService,
    TaskCheckpointConflictError,
    TaskCheckpointCorruptError,
    TaskCheckpointMissingError,
)
from fleet_rlm.workspace.mounted_gateway import DaytonaWorkspaceVolumeGateway


@dataclass(frozen=True)
class _Session:
    id: UUID
    workspace_id: UUID


class _Catalog:
    def __init__(self, session_id: UUID, workspace_id: UUID) -> None:
        self.session = _Session(session_id, workspace_id)
        self.calls: list[tuple[UUID, UUID, UUID]] = []

    async def get(self, session_id: UUID, *, user_id: UUID, workspace_id: UUID) -> _Session:
        self.calls.append((session_id, user_id, workspace_id))
        if session_id != self.session.id or workspace_id != self.session.workspace_id:
            raise LookupError("not authorized")
        return self.session


class _Volume:
    def __init__(self) -> None:
        self.files: dict[tuple[UUID, str], bytes] = {}

    async def read_bytes(self, workspace_id: UUID, path: str, *, max_bytes: int | None = None) -> bytes:
        try:
            raw = self.files[(workspace_id, path)]
        except KeyError as exc:
            raise FileNotFoundError(path) from exc
        if max_bytes is not None and len(raw) > max_bytes:
            raise ValueError("oversized")
        return raw

    async def write_bytes(self, workspace_id: UUID, path: str, data: bytes, *, max_bytes: int | None = None) -> None:
        if max_bytes is not None and len(data) > max_bytes:
            raise ValueError("oversized")
        self.files[(workspace_id, path)] = data


@pytest.fixture
def services() -> tuple[SessionTaskService, _Catalog, _Volume, UUID, UUID, UUID]:
    session_id, workspace_id, user_id = uuid4(), uuid4(), uuid4()
    catalog = _Catalog(session_id, workspace_id)
    volume = _Volume()
    service = SessionTaskService(catalog, volume, VolumePaths.from_mount("/mnt/fleet"))  # type: ignore[arg-type]
    return service, catalog, volume, session_id, workspace_id, user_id


@pytest.mark.asyncio
async def test_seed_once_and_round_trip_update(
    services: tuple[SessionTaskService, _Catalog, _Volume, UUID, UUID, UUID],
) -> None:
    service, catalog, volume, session_id, workspace_id, user_id = services
    seeded = await service.seed(session_id, user_id=user_id, workspace_id=workspace_id, first_request="Build parser")
    assert seeded.revision == 1
    assert seeded.pending_work == ("Build parser",)
    path = f"/mnt/fleet/sessions/{session_id}/task.json"
    assert (workspace_id, path) in volume.files
    assert "workspace/" not in path

    # A restart creates a new service instance over the same volume and keeps the seed.
    restarted = SessionTaskService(catalog, volume, VolumePaths.from_mount("/mnt/fleet"))  # type: ignore[arg-type]
    again = await restarted.seed(
        session_id, user_id=user_id, workspace_id=workspace_id, first_request="Different request"
    )
    assert again == seeded
    updated = await restarted.update(
        session_id,
        user_id=user_id,
        workspace_id=workspace_id,
        expected_revision=1,
        decisions=["Use the existing parser"],
        relevant_paths=["src/parser.py"],
        source_revisions={"src/parser.py": "sha256:abc"},
        completed_work=["Inspect parser"],
    )
    assert updated.revision == 2
    assert updated.pending_work == seeded.pending_work
    assert await restarted.read(session_id, user_id=user_id, workspace_id=workspace_id) == updated
    assert all(call == (session_id, user_id, workspace_id) for call in catalog.calls)


@pytest.mark.asyncio
async def test_long_initial_request_seeds_readable_checkpoint(
    services: tuple[SessionTaskService, _Catalog, _Volume, UUID, UUID, UUID],
) -> None:
    service, _, _, session_id, workspace_id, user_id = services
    request = "  Investigate " + "evidence " * 90
    seeded = await service.seed(session_id, user_id=user_id, workspace_id=workspace_id, first_request=request)
    assert seeded.goal == request
    assert seeded.pending_work == (f"{request.strip()[:497]}...",)
    assert await service.read(session_id, user_id=user_id, workspace_id=workspace_id) == seeded


@pytest.mark.asyncio
@pytest.mark.parametrize("length", (4_000, 4_001, 4_502, 100_000))
async def test_seed_bounds_only_checkpoint_goal(
    services: tuple[SessionTaskService, _Catalog, _Volume, UUID, UUID, UUID], length: int
) -> None:
    service, _, _, session_id, workspace_id, user_id = services
    request = "A" * length
    body = CreateTurnRequest(text=request)
    turn_input = TurnInput(body.text)

    checkpoint = await service.seed(
        session_id, user_id=user_id, workspace_id=workspace_id, first_request=turn_input.text
    )

    expected_goal = request if length <= 4_000 else f"{request[:3_997]}..."
    assert checkpoint.goal == expected_goal
    assert len(checkpoint.goal) <= 4_000
    assert checkpoint.pending_work == (f"{request[:497]}...",)
    assert turn_input.text == request
    assert len(turn_input.text) == length
    assert (
        await service.seed(session_id, user_id=user_id, workspace_id=workspace_id, first_request="B" * length)
        == checkpoint
    )


@pytest.mark.asyncio
async def test_seed_fits_multibyte_request_within_checkpoint_byte_limit(
    services: tuple[SessionTaskService, _Catalog, _Volume, UUID, UUID, UUID],
) -> None:
    service, _, _, session_id, workspace_id, user_id = services
    request = "😀" * 4_000

    checkpoint = await service.seed(session_id, user_id=user_id, workspace_id=workspace_id, first_request=request)

    assert len(checkpoint.goal) <= 4_000
    assert checkpoint.goal.endswith("...")
    assert len(checkpoint.pending_work[0]) <= 500
    assert await service.read(session_id, user_id=user_id, workspace_id=workspace_id) == checkpoint


@pytest.mark.asyncio
async def test_update_rejects_stale_revision_and_unseeded_checkpoint(
    services: tuple[SessionTaskService, _Catalog, _Volume, UUID, UUID, UUID],
) -> None:
    service, _, _, session_id, workspace_id, user_id = services
    with pytest.raises(TaskCheckpointMissingError):
        await service.read(session_id, user_id=user_id, workspace_id=workspace_id)
    await service.seed(session_id, user_id=user_id, workspace_id=workspace_id, first_request="Do work")
    with pytest.raises(TaskCheckpointConflictError):
        await service.update(
            session_id, user_id=user_id, workspace_id=workspace_id, expected_revision=0, goal="overwrite"
        )


@pytest.mark.asyncio
async def test_corrupt_checkpoint_fails_closed(
    services: tuple[SessionTaskService, _Catalog, _Volume, UUID, UUID, UUID],
) -> None:
    service, _, volume, session_id, workspace_id, user_id = services
    path = f"/mnt/fleet/sessions/{session_id}/task.json"
    volume.files[(workspace_id, path)] = b'{"schema_version":1,"revision":2}'
    with pytest.raises(TaskCheckpointCorruptError):
        await service.seed(session_id, user_id=user_id, workspace_id=workspace_id, first_request="Do work")
    with pytest.raises(TaskCheckpointCorruptError):
        await service.read(session_id, user_id=user_id, workspace_id=workspace_id)


@pytest.mark.asyncio
async def test_checkpoint_updates_are_serialized_and_invalid_data_rejected(
    services: tuple[SessionTaskService, _Catalog, _Volume, UUID, UUID, UUID],
) -> None:
    service, _, _, session_id, workspace_id, user_id = services
    await service.seed(session_id, user_id=user_id, workspace_id=workspace_id, first_request="Do work")
    results = await asyncio_gather_updates(service, session_id, user_id, workspace_id)
    assert sum(isinstance(item, TaskCheckpointConflictError) for item in results) == 1
    with pytest.raises(ValueError):
        await service.update(session_id, user_id=user_id, workspace_id=workspace_id, expected_revision=2, goal="   ")


@pytest.mark.asyncio
async def test_checkpoint_round_trips_through_mounted_volume_gateway() -> None:
    class _Fs:
        def __init__(self) -> None:
            self.files: dict[str, bytes] = {}

        async def upload_file(self, data: bytes, path: str) -> None:
            self.files[path] = bytes(data)

        async def download_file(self, path: str) -> bytes:
            try:
                return self.files[path]
            except KeyError as exc:
                raise FileNotFoundError(path) from exc

    class _MountedGateway:
        def __init__(self) -> None:
            self.sandbox = SimpleNamespace(fs=_Fs())

        @asynccontextmanager
        async def open_sandbox(self, workspace_id: UUID, *, purpose: str):
            del workspace_id, purpose
            yield self.sandbox

    session_id, workspace_id, user_id = uuid4(), uuid4(), uuid4()
    catalog = _Catalog(session_id, workspace_id)
    mounted = _MountedGateway()
    paths = VolumePaths.from_mount("/mnt/fleet")
    volume = DaytonaWorkspaceVolumeGateway(mounted, mount_path=str(paths.mount_path))  # type: ignore[arg-type]
    service = SessionTaskService(catalog, volume, paths)  # type: ignore[arg-type]
    first_request = "Résumé 🚀 — 東京"

    seeded = await service.seed(
        session_id,
        user_id=user_id,
        workspace_id=workspace_id,
        first_request=first_request,
    )
    logical_path = str(paths.session_dir(session_id) / "task.json")
    stored = mounted.sandbox.fs.files[logical_path]
    transferred = await volume.read_bytes(workspace_id, logical_path)

    # Compare only bounded metadata so a failed assertion never prints checkpoint bytes.
    assert len(transferred) == len(stored)
    assert sha256(transferred).digest() == sha256(stored).digest()

    loaded = await SessionTaskService(catalog, volume, paths).read(
        session_id,
        user_id=user_id,
        workspace_id=workspace_id,
    )
    assert loaded.revision == seeded.revision == 1
    assert sha256(loaded.goal.encode("utf-8")).digest() == sha256(first_request.encode("utf-8")).digest()


async def asyncio_gather_updates(
    service: SessionTaskService, session_id: UUID, user_id: UUID, workspace_id: UUID
) -> list[object]:
    import asyncio

    async def update(goal: str) -> object:
        try:
            return await service.update(
                session_id,
                user_id=user_id,
                workspace_id=workspace_id,
                expected_revision=1,
                goal=goal,
            )
        except TaskCheckpointConflictError as exc:
            return exc

    return list(await asyncio.gather(update("First"), update("Second")))
