from __future__ import annotations

from types import SimpleNamespace

import pytest

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
        cpu=1,
        mem=1,
        disk=3,
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
    assert (params.resources.cpu, params.resources.memory, params.resources.disk) == (1, 1, 3)
