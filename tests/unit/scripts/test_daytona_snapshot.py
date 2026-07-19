from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.daytona.sandbox_spec import DaytonaSandboxSpec
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


def test_check_requires_active_matching_snapshot() -> None:
    spec = DaytonaSandboxSpec("fleet-test-v1")
    client = SimpleNamespace(snapshot=SimpleNamespace(get=lambda _name: _snapshot(spec)))
    daytona_snapshot.check_snapshot(client, spec)

    with pytest.raises(RuntimeError, match="not active"):
        daytona_snapshot.check_snapshot(
            SimpleNamespace(snapshot=SimpleNamespace(get=lambda _name: _snapshot(spec, state="building"))),
            spec,
        )


def test_check_rejects_snapshot_built_from_a_different_image() -> None:
    spec = DaytonaSandboxSpec("fleet-test-v1")
    snapshot = _snapshot(spec, dockerfile="FROM python:3.13.13-slim-bookworm\n")

    with pytest.raises(RuntimeError, match="image metadata"):
        daytona_snapshot.check_snapshot(
            SimpleNamespace(snapshot=SimpleNamespace(get=lambda _name: snapshot)), spec
        )


def test_create_is_idempotent_without_overwriting_existing_snapshot() -> None:
    spec = DaytonaSandboxSpec("fleet-test-v1")
    create = pytest.fail
    client = SimpleNamespace(
        snapshot=SimpleNamespace(get=lambda _name: _snapshot(spec), create=create),
    )
    daytona_snapshot.create_snapshot(client, spec)


def test_create_builds_with_expected_resources() -> None:
    spec = DaytonaSandboxSpec("fleet-test-v1")
    captured: dict[str, object] = {}

    def get(_name: str) -> object:
        error = FileNotFoundError("missing")
        error.status_code = 404  # type: ignore[attr-defined]
        raise error

    def create(params, *, on_logs):
        captured["params"] = params
        on_logs("internal build log")
        return _snapshot(spec)

    daytona_snapshot.create_snapshot(SimpleNamespace(snapshot=SimpleNamespace(get=get, create=create)), spec)
    params = captured["params"]
    assert params.name == spec.snapshot
    assert (params.resources.cpu, params.resources.memory, params.resources.disk) == (1, 1, 3)
