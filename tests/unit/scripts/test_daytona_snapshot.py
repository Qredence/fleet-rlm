from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import daytona._async.snapshot as snapshot_module
import pytest
from daytona import Image
from daytona._async.snapshot import AsyncSnapshotService

from fleet_rlm.daytona.provisioning import DaytonaSandboxSpec
from scripts import daytona_snapshot


def _snapshot(
    spec: DaytonaSandboxSpec,
    *,
    state: str = "active",
    dockerfile: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=spec.snapshot,
        state=state,
        cpu=spec.cpu,
        mem=spec.memory_gib,
        disk=spec.disk_gib,
        build_info=SimpleNamespace(
            dockerfile_content=dockerfile or daytona_snapshot.build_snapshot_image(spec).dockerfile()
        ),
    )


def test_help_needs_no_credentials(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="0"):
        daytona_snapshot.main(["--help"])
    assert "immutable Fleet Daytona Snapshot" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_check_requires_active_matching_snapshot() -> None:
    spec = DaytonaSandboxSpec("fleet-test-v1")

    async def active(_name: str) -> object:
        return _snapshot(spec)

    client = SimpleNamespace(snapshot=SimpleNamespace(get=active))
    await daytona_snapshot.check_snapshot(client, spec)

    async def building(_name: str) -> object:
        return _snapshot(spec, state="building")

    with pytest.raises(RuntimeError, match="not active"):
        await daytona_snapshot.check_snapshot(
            SimpleNamespace(snapshot=SimpleNamespace(get=building)),
            spec,
        )


@pytest.mark.asyncio
async def test_check_rejects_snapshot_built_from_a_different_image() -> None:
    spec = DaytonaSandboxSpec("fleet-test-v1")
    snapshot = _snapshot(spec, dockerfile="FROM python:3.13.13-slim-bookworm\n")

    async def get(_name: str) -> object:
        return snapshot

    with pytest.raises(RuntimeError, match="image metadata"):
        await daytona_snapshot.check_snapshot(SimpleNamespace(snapshot=SimpleNamespace(get=get)), spec)


@pytest.mark.asyncio
async def test_check_rejects_snapshot_with_dependency_contract_drift() -> None:
    spec = DaytonaSandboxSpec("fleet-test-v1")
    dockerfile = daytona_snapshot.build_snapshot_image(spec).dockerfile()
    snapshot = _snapshot(
        spec,
        dockerfile=dockerfile.replace("mpmath==1.4.1", "mpmath==1.3.0"),
    )

    async def get(_name: str) -> object:
        return snapshot

    with pytest.raises(RuntimeError, match="image metadata"):
        await daytona_snapshot.check_snapshot(SimpleNamespace(snapshot=SimpleNamespace(get=get)), spec)


@pytest.mark.asyncio
async def test_check_rejects_snapshot_with_resource_contract_drift() -> None:
    spec = DaytonaSandboxSpec("fleet-test-v1")
    snapshot = _snapshot(spec)
    snapshot.cpu = 1

    async def get(_name: str) -> object:
        return snapshot

    with pytest.raises(RuntimeError, match="resources"):
        await daytona_snapshot.check_snapshot(SimpleNamespace(snapshot=SimpleNamespace(get=get)), spec)


@pytest.mark.asyncio
async def test_create_is_idempotent_without_overwriting_existing_snapshot() -> None:
    spec = DaytonaSandboxSpec("fleet-test-v1")
    create = pytest.fail

    async def get(_name: str) -> object:
        return _snapshot(spec)

    client = SimpleNamespace(
        snapshot=SimpleNamespace(get=get, create=create),
    )
    await daytona_snapshot.create_snapshot(client, spec)


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_snapshot_sdk_skips_build_context_upload_for_fleet_image() -> None:
    """Fleet's declarative image has no local build context to upload."""
    spec = DaytonaSandboxSpec("fleet-test-v1")

    class ObjectStorageAPI:
        async def get_push_access(self) -> object:
            pytest.fail("empty Fleet image must not request object-storage credentials")

    assert (
        await AsyncSnapshotService.process_image_context(
            ObjectStorageAPI(), daytona_snapshot.build_snapshot_image(spec)
        )
        == []
    )


@pytest.mark.asyncio
async def test_snapshot_sdk_uploads_local_build_context_once_without_fleet_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exercise Daytona's uploader path separately from sandbox filesystem I/O."""
    source = tmp_path / "context.txt"
    source.write_text("small build context", encoding="utf-8")
    image = Image.base("python:3.13-slim").add_local_file(source, "/opt/context.txt")
    calls: list[tuple[str, str, str]] = []

    class PushAccess:
        storage_url = "https://object-storage.invalid"
        access_key = "access"
        secret = "secret"
        session_token = "session"
        bucket = "bucket"
        region = "region"
        organization_id = "organization"

    class ObjectStorageAPI:
        async def get_push_access(self) -> PushAccess:
            return PushAccess()

    class Storage:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def upload(self, source_path: str, organization_id: str, archive_path: str) -> str:
            calls.append((source_path, organization_id, archive_path))
            return "uploaded-context-hash"

    monkeypatch.setattr(snapshot_module, "AsyncObjectStorage", Storage)
    hashes = await AsyncSnapshotService.process_image_context(ObjectStorageAPI(), image)

    assert hashes == ["uploaded-context-hash"]
    assert len(calls) == 1
    assert calls[0][0] == str(source)
    assert calls[0][1] == "organization"
    assert calls[0][2].endswith("context.txt")


@pytest.mark.asyncio
async def test_create_builds_with_expected_resources() -> None:
    spec = DaytonaSandboxSpec("fleet-test-v1")
    captured: dict[str, object] = {}

    async def get(_name: str) -> object:
        error = FileNotFoundError("missing")
        error.status_code = 404  # type: ignore[attr-defined]
        raise error

    async def create(params, *, on_logs):
        captured["params"] = params
        on_logs("internal build log")
        return _snapshot(spec)

    await daytona_snapshot.create_snapshot(SimpleNamespace(snapshot=SimpleNamespace(get=get, create=create)), spec)
    params = captured["params"]
    assert params.name == spec.snapshot
    assert (params.resources.cpu, params.resources.memory, params.resources.disk) == (4, 8, 8)
